"""Verification test suite for BB84 simulation engine.

Run with:
    pytest -v

Tests are deterministic because fixed random seeds are passed to each run.
Tolerances are derived from expected statistical distributions (e.g. Binomial).
"""
import numpy as np
import pytest

from engine import binary_entropy, error_correct, run_bb84

N_BIG = 4000


# ---------------- The five core required tests ----------------

def test_no_eve_no_noise_qber_is_exactly_zero():
    """An ideal quantum channel with no eavesdropper and zero noise produces 0% QBER."""
    r = run_bb84(n=1000, eve_prob=0.0, noise_prob=0.0, seed=1)
    assert r.true_qber == 0.0
    assert r.estimated_qber == 0.0
    assert not r.aborted


def test_eve_full_intercept_qber_about_quarter():
    """100% intercept-resend attack introduces ~25% QBER in the sifted key (p/4 rule).

    For ~2000 sifted bits, the standard deviation is ~0.0097, so +/- 0.03 is ~3 sigma.
    """
    r = run_bb84(n=N_BIG, eve_prob=1.0, noise_prob=0.0, seed=2)
    assert r.true_qber == pytest.approx(0.25, abs=0.03)


def test_noise_only_qber_about_five_percent():
    """A 5% channel bit-flip probability induces ~5% QBER in the sifted key.

    For ~2000 sifted bits, standard deviation is ~0.005, so +/- 0.02 is ~4 sigma.
    """
    r = run_bb84(n=N_BIG, eve_prob=0.0, noise_prob=0.05, seed=3)
    assert r.true_qber == pytest.approx(0.05, abs=0.02)


def test_sifted_length_about_half_of_n():
    """Independent random basis selection retains approximately 50% of transmitted qubits.

    Sifted length follows Binomial(4000, 0.5) with std dev ~31.6. A 5% tolerance (200) is ~6 sigma.
    """
    r = run_bb84(n=N_BIG, seed=4)
    assert abs(len(r.sifted_idx) - N_BIG / 2) < 0.05 * N_BIG


def test_same_seed_gives_identical_results():
    """A fixed random seed guarantees identical quantum circuit measurements and key bits."""
    a = run_bb84(n=300, eve_prob=0.5, noise_prob=0.05, seed=123)
    b = run_bb84(n=300, eve_prob=0.5, noise_prob=0.05, seed=123)
    assert np.array_equal(a.alice_bits, b.alice_bits)
    assert np.array_equal(a.bob_bits, b.bob_bits)
    assert np.array_equal(a.eve_bits, b.eve_bits)
    assert a.estimated_qber == b.estimated_qber
    assert np.array_equal(a.final_key_alice, b.final_key_alice)

    # Different seeds must produce different key realizations
    c = run_bb84(n=300, eve_prob=0.5, noise_prob=0.05, seed=124)
    assert not np.array_equal(a.alice_bits, c.alice_bits)


# ---------------- Additional protocol validation tests ----------------

def test_bob_results_are_random_when_bases_differ():
    """Measuring in the wrong basis yields 50/50 outcomes uncorrelated with Alice's bit."""
    r = run_bb84(n=2000, seed=5)
    mism = ~r.match
    frac_ones = r.bob_bits[mism].mean()
    assert 0.45 < frac_ones < 0.55

    agree = (r.bob_bits[mism] == r.alice_bits[mism]).mean()
    assert 0.45 < agree < 0.55


def test_sample_is_discarded_and_counted():
    """Sampled bits are measured for QBER and discarded from the remaining raw key."""
    r = run_bb84(n=400, sample_frac=0.25, seed=6)
    assert len(r.sample_idx) == round(0.25 * len(r.sifted_idx))
    assert len(r.key_alice) == len(r.sifted_idx) - len(r.sample_idx)
    assert set(r.sample_idx).isdisjoint(r.remaining_idx)


def test_abort_when_qber_over_threshold():
    """When estimated QBER exceeds the security threshold, the protocol safely aborts."""
    r = run_bb84(n=1000, eve_prob=1.0, seed=7)
    assert r.aborted
    assert len(r.final_key_alice) == 0


def test_error_correction_reduces_errors_and_counts_leak():
    """Parity checking reduces bit discrepancies and accurately counts leaked parity bits."""
    rng = np.random.default_rng(0)
    alice = rng.integers(0, 2, 500)
    bob = alice.copy()
    bob[rng.choice(500, 15, replace=False)] ^= 1  # 3% synthetic errors
    fixed, leaked = error_correct(alice, bob, 0.03, rng)
    assert (fixed != alice).sum() < (bob != alice).sum()
    assert leaked > 0


def test_final_key_matches_and_length_formula():
    """Privacy amplification produces keys of length floor(L - leaked - L*h2(QBER))."""
    r = run_bb84(n=2000, eve_prob=0.0, noise_prob=0.02, seed=8)
    L = len(r.key_alice)
    expected = int(np.floor(L - r.leaked_bits - L * binary_entropy(r.estimated_qber)))
    assert len(r.final_key_alice) == max(0, expected)
    if r.residual_errors == 0:
        assert np.array_equal(r.final_key_alice, r.final_key_bob)


def test_binary_entropy_values():
    """Binary Shannon entropy satisfies boundary values h2(0)=h2(1)=0 and maximum h2(0.5)=1."""
    assert binary_entropy(0) == 0 and binary_entropy(1) == 0
    assert binary_entropy(0.5) == pytest.approx(1.0)
