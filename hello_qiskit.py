"""Milestone 1 sanity check: verify Qiskit and AerSimulator in this environment.

Tests single-circuit execution and multi-circuit batching in one run call.
"""
from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator

sim = AerSimulator()

# 1. Single circuit: Hadamard on |0> creates equal superposition (|0> + |1>)/sqrt(2)
qc = QuantumCircuit(1, 1)
qc.h(0)
qc.measure(0, 0)
print(qc.draw("text"))
counts = sim.run(qc, shots=1000, seed_simulator=1).result().get_counts()
print("H then measure, 1000 shots:", counts)

# 2. Batch of circuits in ONE call (pattern used by engine.py), 1 shot each
# Circuit A: X then measure -> deterministic 1
# Circuit B: identity then measure -> deterministic 0
# Circuit C: H then H (self-inverse) then measure -> deterministic 0
circs = []
for gates in (["x"], [], ["h", "h"]):
    c = QuantumCircuit(1, 1)
    for g in gates:
        getattr(c, g)(0)
    c.measure(0, 0)
    circs.append(c)

result = sim.run(circs, shots=1).result()
print("Batch of 3 circuits, 1 shot each:",
      [result.get_counts(i) for i in range(len(circs))])
