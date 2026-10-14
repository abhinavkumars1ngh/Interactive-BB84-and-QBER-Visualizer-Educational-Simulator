"""BB84 engine: pure quantum logic and protocol math.

Implements the complete 8-stage BB84 Quantum Key Distribution protocol:
  1. Alice state preparation: encodes random classical bits into single-photon
     polarization states across two mutually unbiased bases (+ rectilinear and × diagonal).
  2. Eve intercept-resend attack (optional): measures in a random basis, collapsing the
     photon state, then prepares and transmits a fresh photon based on her outcome.
  3. Channel noise (optional): applies an environment-induced bit-flip in the
     active transmission basis (X gate for +, Z gate for ×).
  4. Bob measurement: measures incoming photons in randomly chosen bases (+ or ×).
     (Stages 1-4 execute as 1-qubit QuantumCircuits batched into a single AerSimulator call).
  5. Sifting: Alice and Bob publicly announce bases (never bits) and discard positions
     where their bases differed (~50% retention).
  6. QBER estimation & decision: reveal and sacrifice a random sample of the sifted key
     to estimate the Quantum Bit Error Rate. If QBER exceeds 11%, eavesdropping is detected
     and the protocol aborts.
  7. Error correction: reconciles bit disagreements between Alice and Bob via a 2-pass
     Cascade-style parity comparison and binary search, tracking leaked parity bits.
  8. Privacy amplification: hashes the reconciled key with a random binary matrix to
     compress out any partial information Eve could have gained.

Conventions:
  Basis 0 = rectilinear (+): |0> (bit 0), |1> (bit 1)
  Basis 1 = diagonal (×):    |+> = H|0> (bit 0), |-> = H|1> (bit 1)
"""
from dataclasses import dataclass, field

import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
from qiskit_aer import AerSimulator

RECTILINEAR, DIAGONAL = 0, 1


@dataclass
class BB84Result:
    """Stores intermediate arrays, diagnostic flags, and final keys for all protocol stages."""
    # --- Input configuration ---
    n: int
    eve_prob: float
    noise_prob: float
    sample_frac: float
    abort_threshold: float
    seed: int | None

    # --- Stage 1: Alice ---
    alice_bits: np.ndarray = None
    alice_bases: np.ndarray = None

    # --- Stage 2: Eve (-1 indicates qubit was not intercepted) ---
    eve_intercepted: np.ndarray = None
    eve_bases: np.ndarray = None
    eve_bits: np.ndarray = None

    # --- Stage 3: Channel noise ---
    flipped: np.ndarray = None

    # --- Stage 4: Bob ---
    bob_bases: np.ndarray = None
    bob_bits: np.ndarray = None

    # --- Stage 5: Sifting ---
    match: np.ndarray = None
    error: np.ndarray = None
    sifted_idx: np.ndarray = None

    # --- Stage 6: QBER estimation ---
    sample_idx: np.ndarray = None
    remaining_idx: np.ndarray = None
    estimated_qber: float = 0.0
    true_qber: float = 0.0
    aborted: bool = False
    key_alice: np.ndarray = None
    key_bob: np.ndarray = None

    # --- Stage 7: Error correction ---
    corrected_bob: np.ndarray = None
    leaked_bits: int = 0
    residual_errors: int = 0

    # --- Stage 8: Privacy amplification ---
    hash_matrix: np.ndarray = None
    final_key_alice: np.ndarray = field(default_factory=lambda: np.array([], dtype=int))
    final_key_bob: np.ndarray = field(default_factory=lambda: np.array([], dtype=int))


def binary_entropy(p: float) -> float:
    """Calculate the binary Shannon entropy h2(p) = -p*log2(p) - (1-p)*log2(1-p).

    Quantifies the uncertainty (in bits) associated with a binary error probability.
    Used in privacy amplification to calculate how many bits of information Eve
    could have acquired from channel errors and public reconciliation leakage.

    Parameters:
        p (float): Error probability or QBER in range [0, 1].

    Returns:
        float: Entropy in bits [0.0, 1.0], with h2(0) = h2(1) = 0.0.
    """
    if p <= 0 or p >= 1:
        return 0.0
    return float(-p * np.log2(p) - (1 - p) * np.log2(1 - p))


def build_qubit_circuit(alice_bit, alice_basis, eve=False, eve_basis=0,
                        flip=False, bob_basis=0) -> QuantumCircuit:
    """Build a 1-qubit QuantumCircuit for one photon: Alice -> Eve -> Noise -> Bob.

    Simulates the physical quantum channel:
      1. Alice prepares |0>, flips to |1> via X if bit=1, and rotates to diagonal basis
         via Hadamard H if basis=diagonal.
      2. Eve (if intercepting) measures in her basis. To obey quantum mechanics, this
         collapses the state. Eve then discards (resets) the photon and prepares a brand
         new photon in her measured state to forward to Bob.
      3. Channel noise flips the encoded bit in the active basis. A plain X gate only
         flips states in the + basis (since X|+> = |+>); in the × basis, a phase-flip Z
         gate is required to flip |+> to |->.
      4. Bob measures in his basis (applying H first if measuring in diagonal basis).

    Parameters:
        alice_bit (int): Bit value to send (0 or 1).
        alice_basis (int): Alice's basis (0 = +, 1 = ×).
        eve (bool): Whether Eve intercepts this photon.
        eve_basis (int): Eve's basis if intercepting (0 = +, 1 = ×).
        flip (bool): Whether channel noise flips the qubit in transit.
        bob_basis (int): Bob's basis (0 = +, 1 = ×).

    Returns:
        QuantumCircuit: Constructed circuit with classical registers for Eve and Bob.
    """
    q = QuantumRegister(1, "q")
    eve_c = ClassicalRegister(1, "eve")
    bob_c = ClassicalRegister(1, "bob")
    qc = QuantumCircuit(q, eve_c, bob_c)

    # Alice encodes: X prepares |1>, H rotates to |+> or |->
    if alice_bit:
        qc.x(0)
    if alice_basis == DIAGONAL:
        qc.h(0)
    qc.barrier()

    if eve:
        # Eve measures: H rotates diagonal basis to computational Z basis
        if eve_basis == DIAGONAL:
            qc.h(0)
        qc.measure(0, eve_c[0])

        # Intercept-resend: collapse destroys the original state.
        # Eve resets the qubit and emits a fresh photon matching her classical result.
        qc.reset(0)
        with qc.if_test((eve_c, 1)):
            qc.x(0)
        if eve_basis == DIAGONAL:
            qc.h(0)
        qc.barrier()

    if flip:
        # Channel noise: basis-aware bit flip.
        # Active basis is Eve's if she re-prepared the photon, otherwise Alice's.
        # X flips computational states (|0> <-> |1>); Z flips diagonal states (|+> <-> |->).
        current_basis = eve_basis if eve else alice_basis
        if current_basis == DIAGONAL:
            qc.z(0)
        else:
            qc.x(0)
        qc.barrier()

    # Bob measures: H rotates diagonal basis back to computational basis
    if bob_basis == DIAGONAL:
        qc.h(0)
    qc.measure(0, bob_c[0])
    return qc


def _binary_search_fix(alice, bob, positions):
    """Locate and correct a single bit error in an odd-parity block via binary search.

    When Alice and Bob detect different block parities, an odd number of bit errors
    must exist. By recursively splitting the block in half and exchanging 1 parity bit
    per step, exactly one error position is located in log2(block_size) steps.

    Parameters:
        alice (np.ndarray): Alice's key bits.
        bob (np.ndarray): Bob's key bits (corrected in-place).
        positions (np.ndarray): Key indices belonging to this block.

    Returns:
        int: Total number of parity bits announced publicly during this search.
    """
    leaked = 0
    lo, hi = 0, len(positions)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        left = positions[lo:mid]
        leaked += 1
        if alice[left].sum() % 2 != bob[left].sum() % 2:
            hi = mid
        else:
            lo = mid
    pos = positions[lo]
    bob[pos] ^= 1
    return leaked


def error_correct(alice_key, bob_key, qber, rng):
    """Reconcile errors between Alice's and Bob's keys (simplified 2-pass Cascade).

    Divides keys into blocks and compares block parities. Blocks with matching
    parities are assumed correct (or contain an even number of errors). Blocks with
    differing parities trigger binary search to fix one error. Two passes with a
    random permutation between them separate error clusters.

    Parameters:
        alice_key (array-like): Alice's raw key bits.
        bob_key (array-like): Bob's raw key bits.
        qber (float): Estimated error rate, used to set initial block size ~ 0.73 / QBER.
        rng (np.random.Generator): Random generator for block permutation.

    Returns:
        tuple[np.ndarray, int]: Corrected copy of Bob's key, and total leaked parity bits.
    """
    alice = np.array(alice_key, dtype=int)
    bob = np.array(bob_key, dtype=int)
    L = len(alice)
    if L == 0:
        return bob, 0

    # Initial block size: Cascade heuristic k ~ 0.73 / QBER targets <= 1 error per block
    k1 = L if qber <= 0 else int(np.clip(round(0.73 / qber), 4, L))
    leaked = 0

    # Pass 1 with block size k1, Pass 2 with doubled block size to catch remaining pairs
    for block_size in (k1, min(2 * k1, L)):
        order = rng.permutation(L)
        for start in range(0, L, block_size):
            positions = order[start:start + block_size]
            leaked += 1
            if alice[positions].sum() % 2 != bob[positions].sum() % 2:
                leaked += _binary_search_fix(alice, bob, positions)
    return bob, leaked


def run_bb84(n, eve_prob=0.0, noise_prob=0.0, sample_frac=0.5,
             abort_threshold=0.11, seed=None) -> BB84Result:
    """Execute the complete BB84 protocol end-to-end on simulated quantum circuits.

    Runs Alice's preparation, optional Eve eavesdropping, channel noise, and Bob's
    measurement as real Qiskit circuits batched into AerSimulator in one call.
    Then performs classical post-processing: sifting, QBER estimation, threshold abort,
    parity error correction, and matrix-hashing privacy amplification.

    Parameters:
        n (int): Number of qubits Alice prepares and sends.
        eve_prob (float): Probability of Eve intercepting each qubit [0.0, 1.0].
        noise_prob (float): Channel bit-flip probability [0.0, 1.0].
        sample_frac (float): Fraction of sifted key revealed to estimate QBER [0.05, 1.0].
        abort_threshold (float): Security threshold (default 0.11). If QBER > threshold,
            eavesdropping cannot be ruled out and the protocol aborts.
        seed (int, optional): Random seed ensuring exact reproducibility.

    Returns:
        BB84Result: Comprehensive dataclass containing all states, metrics, and keys.
    """
    rng = np.random.default_rng(seed)
    r = BB84Result(n, eve_prob, noise_prob, sample_frac, abort_threshold, seed)

    # Stage 1-4: Classical random choices for every qubit
    r.alice_bits = rng.integers(0, 2, n)
    r.alice_bases = rng.integers(0, 2, n)
    r.eve_intercepted = rng.random(n) < eve_prob
    eve_bases = rng.integers(0, 2, n)
    r.flipped = rng.random(n) < noise_prob
    r.bob_bases = rng.integers(0, 2, n)

    # Build one quantum circuit per qubit and execute the entire batch in 1 AerSimulator call
    circuits = [
        build_qubit_circuit(r.alice_bits[i], r.alice_bases[i],
                            eve=r.eve_intercepted[i], eve_basis=eve_bases[i],
                            flip=r.flipped[i], bob_basis=r.bob_bases[i])
        for i in range(n)
    ]
    sim = AerSimulator()
    result = sim.run(circuits, shots=1, memory=True, seed_simulator=seed).result()

    outcomes = [result.get_memory(i)[0].split() for i in range(n)]
    r.bob_bits = np.array([int(o[0]) for o in outcomes])
    eve_raw = np.array([int(o[1]) for o in outcomes])

    r.eve_bases = np.where(r.eve_intercepted, eve_bases, -1)
    r.eve_bits = np.where(r.eve_intercepted, eve_raw, -1)

    # Stage 5: Sifting — discard positions where bases did not match
    r.match = r.alice_bases == r.bob_bases
    r.error = r.match & (r.alice_bits != r.bob_bits)
    r.sifted_idx = np.flatnonzero(r.match)
    n_sift = len(r.sifted_idx)
    r.true_qber = float(r.error.sum() / n_sift) if n_sift else 0.0

    # Stage 6: Sample and estimate QBER; sacrifice sample bits to protect key privacy
    n_sample = min(n_sift, max(1, round(sample_frac * n_sift))) if n_sift else 0
    chosen = rng.permutation(n_sift)
    r.sample_idx = np.sort(r.sifted_idx[chosen[:n_sample]])
    r.remaining_idx = np.sort(r.sifted_idx[chosen[n_sample:]])
    r.estimated_qber = (float(r.error[r.sample_idx].sum() / n_sample)
                        if n_sample else 0.0)

    # Security check: abort if estimated error exceeds theoretical threshold (~11%)
    r.aborted = r.estimated_qber > abort_threshold
    r.key_alice = r.alice_bits[r.remaining_idx]
    r.key_bob = r.bob_bits[r.remaining_idx]

    if r.aborted:
        r.corrected_bob = r.key_bob.copy()
        return r

    # Stage 7: Error correction (reconciliation)
    r.corrected_bob, r.leaked_bits = error_correct(
        r.key_alice, r.key_bob, r.estimated_qber, rng)
    r.residual_errors = int((r.corrected_bob != r.key_alice).sum())

    # Stage 8: Privacy amplification
    # Secure length formula: L_final = L - leaked - L * h2(QBER)
    # Compresses out both public parity leakage and Eve's potential mutual information
    L = len(r.key_alice)
    final_len = int(np.floor(L - r.leaked_bits - L * binary_entropy(r.estimated_qber)))
    final_len = max(0, final_len)

    # Universal-2 hash using a shared random binary matrix modulo 2
    r.hash_matrix = rng.integers(0, 2, (final_len, L))
    r.final_key_alice = (r.hash_matrix @ r.key_alice) % 2
    r.final_key_bob = (r.hash_matrix @ r.corrected_bob) % 2
    return r
