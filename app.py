"""BB84 & QBER Visualizer: Streamlit UI only. All protocol logic is in engine.py.

Run with:  streamlit run app.py
"""
import threading
import time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine import binary_entropy, build_qubit_circuit, run_bb84


def in_big_thread(fn, *args, **kwargs):
    """Run a function inside a worker thread allocated with a 64 MB stack.

    Streamlit executes app reruns on worker threads that default to 512 KB on macOS
    (ARM64). Qiskit's Rust/C++ circuit compiler overflows this small stack on consecutive
    runs and causes a segmentation fault. Running in a 64 MB stack thread prevents the crash.

    Parameters:
        fn (callable): Function to execute.
        *args, **kwargs: Arguments passed directly to fn.

    Returns:
        Any: Return value of fn(*args, **kwargs).
    """
    out = {}

    def target():
        try:
            out["value"] = fn(*args, **kwargs)
        except BaseException as e:
            out["error"] = e

    old = threading.stack_size(64 * 1024 * 1024)
    try:
        t = threading.Thread(target=target)
        t.start()
    finally:
        threading.stack_size(old)
    t.join()
    if "error" in out:
        raise out["error"]
    return out["value"]


def synced_input(label, min_val, max_val, default, step, key):
    """Render a number input and slider kept in two-way sync via session_state.

    Typing into the number input updates the slider; moving the slider updates
    the number input. Both share the same min, max, and step boundaries.

    Parameters:
        label (str): User-visible label for the input.
        min_val (int): Minimum allowable value.
        max_val (int): Maximum allowable value.
        default (int): Initial value when the app first loads.
        step (int): Increment/decrement step size.
        key (str): Base key used for st.session_state storage.

    Returns:
        int: The currently selected value.
    """
    num_key = f"{key}_num"
    slider_key = f"{key}_slider"

    if num_key not in st.session_state:
        st.session_state[num_key] = default
    if slider_key not in st.session_state:
        st.session_state[slider_key] = default

    def on_num_change():
        st.session_state[slider_key] = st.session_state[num_key]

    def on_slider_change():
        st.session_state[num_key] = st.session_state[slider_key]

    st.number_input(
        label,
        min_value=min_val,
        max_value=max_val,
        step=step,
        key=num_key,
        on_change=on_num_change,
    )
    st.slider(
        label,
        min_value=min_val,
        max_value=max_val,
        step=step,
        key=slider_key,
        on_change=on_slider_change,
        label_visibility="collapsed",
    )
    return st.session_state[num_key]


st.set_page_config(page_title="BB84 & QBER Visualizer", layout="wide")

st.title("BB84 & QBER Visualizer")
st.caption("Real Qiskit circuits on AerSimulator. Basis 0 = rectilinear (+), 1 = diagonal (×).")

# ---------------- sidebar: parameters ----------------
with st.sidebar:
    st.header("Parameters")
    n = synced_input("Number of qubits", 20, 2000, 100, 10, "n")
    eve_pct = synced_input("Eve intercept probability (%)", 0, 100, 0, 1, "eve")
    noise_pct = synced_input("Channel noise, bit-flip (%)", 0, 30, 0, 1, "noise")
    sample_pct = synced_input("Sample size for QBER estimate (% of sifted key)", 5, 100, 50, 1, "sample")
    thr_pct = synced_input("Abort threshold (% QBER)", 1, 30, 11, 1, "thr")
    seed = st.number_input("Random seed", min_value=0, value=42, step=1)


@st.cache_data(show_spinner="Running BB84 circuits...")
def cached_run(n, eve, noise, sample, thr, seed):
    """Cache simulation results by parameter values to avoid redundant circuit execution."""
    return in_big_thread(run_bb84, n, eve, noise, sample, thr, seed)


r = cached_run(n, eve_pct / 100, noise_pct / 100, sample_pct / 100, thr_pct / 100, int(seed))

tab_protocol, tab_experiment = st.tabs(["Protocol", "Experiment"])

# ---------------- helpers for the table ----------------
SYMBOL = {(0, 0): "↑", (0, 1): "→", (1, 0): "↗", (1, 1): "↖"}
BASIS_NAME = {0: "+", 1: "×", -1: "–"}
MAX_ROWS = 200

GREEN, RED, GREY, PINK = "#1e5631", "#7a1f1f", "#2b2b2b", "#5c3a1e"


def colour_for_row(i):
    """Return row color: green (kept & matching), red (sifted error), or grey (discarded)."""
    if not r.match[i]:
        return GREY
    return RED if r.error[i] else GREEN


def styled(df, colours):
    """Apply row-level background styling to a DataFrame for visual inspection."""
    return df.style.apply(lambda row: [f"background-color: {colours[row.name]}"] * len(row), axis=1)


def show_table(df, colours):
    """Render a styled table, displaying up to MAX_ROWS rows to maintain UI responsiveness."""
    if len(df) > MAX_ROWS:
        st.caption(f"Showing the first {MAX_ROWS} of {len(df)} rows.")
        df, colours = df.iloc[:MAX_ROWS], colours
    st.dataframe(styled(df, colours), height=320)


idx = np.arange(r.n)
base_df = pd.DataFrame({
    "Alice bit": r.alice_bits,
    "Alice basis": [BASIS_NAME[b] for b in r.alice_bases],
    "Photon sent": [SYMBOL[(b, v)] for b, v in zip(r.alice_bases, r.alice_bits)],
    "Eve intercepted": np.where(r.eve_intercepted, "yes", "no"),
    "Eve basis": [BASIS_NAME[b] for b in r.eve_bases],
    "Eve bit": ["–" if b < 0 else str(int(b)) for b in r.eve_bits],
    "Noise flip": np.where(r.flipped, "yes", "no"),
    "Bob basis": [BASIS_NAME[b] for b in r.bob_bases],
    "Bob bit": r.bob_bits,
    "Match": np.where(r.match, "✓", "✗"),
    "Error": np.where(r.error, "ERROR", np.where(r.match, "ok", "")),
})
row_colours = [colour_for_row(i) for i in idx]
eve_colours = [PINK if r.eve_intercepted[i] else GREY for i in idx]

# ---------------- Protocol tab ----------------
with tab_protocol:
    sifted = len(r.sifted_idx)
    c = st.columns(5)
    c[0].metric("Sifted length", sifted)
    c[1].metric("Estimated QBER", f"{100 * r.estimated_qber:.2f}%")
    c[2].metric("True QBER (whole sifted key)", f"{100 * r.true_qber:.2f}%")
    c[3].metric("Final key length", len(r.final_key_alice))
    c[4].metric("Result", "ABORT" if r.aborted else "ACCEPT")
    st.caption("Table colours: green = bases match & bits agree, red = error in sifted key, "
               "grey = discarded (bases differ) or no Eve, brown = intercepted by Eve.")

    stages = st.tabs(["1 Prepare", "2 Eve", "3 Measure", "4 Sift",
                      "5 QBER estimate", "6 Error correction", "7 Privacy amplification"])

    with stages[0]:
        st.write("**Alice picks a random bit and basis for each qubit and encodes it as a photon polarization.**")
        show_table(base_df[["Alice bit", "Alice basis", "Photon sent"]], [GREY] * r.n)

    with stages[1]:
        st.write("**Eve measures some qubits in a random basis and sends on a fresh qubit prepared from her result.**")
        st.write(f"Intercepted {int(r.eve_intercepted.sum())} of {r.n} qubits.")
        show_table(base_df[["Alice bit", "Alice basis", "Eve intercepted", "Eve basis", "Eve bit"]], eve_colours)

    with stages[2]:
        st.write("**Bob measures each qubit in a random basis; a wrong basis gives a random result.**")
        st.write(f"Channel noise flipped {int(r.flipped.sum())} qubits.")
        show_table(base_df[["Noise flip", "Bob basis", "Bob bit"]], [GREY] * r.n)

    with stages[3]:
        st.write("**Alice and Bob publicly compare bases (not bits) and keep only positions where they match.**")
        st.write(f"Kept {sifted} of {r.n} qubits ({100 * sifted / r.n:.1f}%).")
        show_table(base_df[["Alice basis", "Bob basis", "Match"]], [GREY if not m else GREEN for m in r.match])

    with stages[4]:
        st.write("**They reveal a random sample of the sifted key and count mismatches; the sample is then discarded.**")
        st.write(f"Sample: {len(r.sample_idx)} bits, "
                 f"{int(r.error[r.sample_idx].sum())} mismatches, "
                 f"estimated QBER = {100 * r.estimated_qber:.2f}% "
                 f"(true QBER over all {sifted} sifted bits = {100 * r.true_qber:.2f}%). "
                 f"Threshold {thr_pct}% -> **{'ABORT' if r.aborted else 'ACCEPT'}**.")
        role = np.full(r.n, "", dtype=object)
        role[r.sample_idx] = "sample (revealed)"
        role[r.remaining_idx] = "raw key"
        sample_df = base_df[["Alice bit", "Bob bit", "Error"]].copy()
        sample_df["Role"] = role
        sample_df = sample_df[r.match]
        colours = {i: colour_for_row(i) for i in r.sifted_idx}
        st.dataframe(sample_df.style.apply(
            lambda row: [f"background-color: {colours[row.name]}"] * len(row), axis=1), height=320)

    with stages[5]:
        st.write("**Block parities and binary search find and fix differences, at the cost of leaked bits (2 passes).**")
        if r.aborted:
            st.warning("Skipped: the run was aborted at the QBER check.")
        else:
            before = int((r.key_bob != r.key_alice).sum())
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Raw key length", len(r.key_alice))
            c2.metric("Errors before", before)
            c3.metric("Errors after", r.residual_errors)
            c4.metric("Bits leaked", r.leaked_bits)

    with stages[6]:
        st.write("**A random binary matrix hashes the corrected key down to a shorter, secret key.**")
        if r.aborted:
            st.warning("Skipped: the run was aborted at the QBER check.")
        else:
            L = len(r.key_alice)
            st.latex(rf"\text{{final length}} = \lfloor L - \text{{leaked}} - L\,h_2(QBER) \rfloor"
                     rf" = \lfloor {L} - {r.leaked_bits} - {L}\cdot{binary_entropy(r.estimated_qber):.3f} \rfloor"
                     rf" = {len(r.final_key_alice)}")
            same = np.array_equal(r.final_key_alice, r.final_key_bob)
            st.write(f"Alice and Bob's final keys {'**match**' if same else '**differ** (residual errors)'}.")
            st.code("Alice: " + "".join(map(str, r.final_key_alice[:128])) + "\n"
                    "Bob:   " + "".join(map(str, r.final_key_bob[:128])))

    # ---- example circuit diagram ----
    st.subheader("Example qubit circuit: Alice → Eve → Bob")
    example = st.number_input("Qubit number", 0, r.n - 1, 0, step=1)
    i = int(example)
    qc = in_big_thread(build_qubit_circuit,
        r.alice_bits[i], r.alice_bases[i], eve=bool(r.eve_intercepted[i]),
        eve_basis=max(0, int(r.eve_bases[i])), flip=bool(r.flipped[i]), bob_basis=r.bob_bases[i])
    st.write(f"Qubit {i}: Alice bit {r.alice_bits[i]} in basis {BASIS_NAME[r.alice_bases[i]]}, "
             f"Eve {'intercepts' if r.eve_intercepted[i] else 'does not intercept'}, "
             f"noise flip {'yes' if r.flipped[i] else 'no'}, "
             f"Bob basis {BASIS_NAME[r.bob_bases[i]]} -> Bob bit {r.bob_bits[i]}.")
    st.code(in_big_thread(lambda: str(qc.draw(output="text", fold=-1))), language=None)

# ---------------- Experiment tab ----------------
with tab_experiment:
    st.write("Average QBER of the sifted key as Eve's intercept probability goes from 0 to 1 "
             "(no channel noise, so the theory line is QBER = p/4).")
    runs = st.slider("Runs per point", 2, 30, 8)
    n_exp = st.slider("Qubits per run", 100, 1000, 400, step=50)
    if st.button("Run experiment"):
        ps = np.linspace(0, 1, 11)
        means, stds = [], []
        bar = st.progress(0.0)
        t0 = time.time()
        for k, p in enumerate(ps):
            vals = [in_big_thread(run_bb84, n_exp, p, 0.0, 0.5, thr_pct / 100, int(seed) * 1000 + k * 100 + j).true_qber
                    for j in range(runs)]
            means.append(np.mean(vals))
            stds.append(np.std(vals))
            bar.progress((k + 1) / len(ps))
        st.session_state["experiment"] = (ps, means, stds, time.time() - t0, runs, n_exp, thr_pct)
    if "experiment" in st.session_state:
        ps, means, stds, secs, runs_d, n_d, thr_d = st.session_state["experiment"]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=ps, y=means, mode="markers+lines", name="Simulated (mean ± std)",
                                 error_y=dict(type="data", array=stds)))
        fig.add_trace(go.Scatter(x=[0, 1], y=[0, 0.25], mode="lines", name="Theory: QBER = p/4",
                                 line=dict(dash="dash")))
        fig.add_hline(y=thr_d / 100, line_dash="dot", line_color="red",
                      annotation_text=f"Abort threshold {thr_d}%")
        fig.update_layout(xaxis_title="Eve intercept probability p", yaxis_title="Sifted QBER",
                          yaxis_tickformat=".0%", height=450)
        st.plotly_chart(fig)
        st.caption(f"{runs_d} runs per point x 11 points, {n_d} qubits per run; took {secs:.1f} s.")
