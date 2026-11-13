# Examples

Three small, runnable explanations of what the exploratory stage claims and what it does not. Each runs offline on synthetic data (`tests/eda_support.py`: seeded, no clock, no network), prints a table, says how to read it, and is run as a test in CI (`tests/test_eda_examples.py`), so what the page below shows cannot drift from what the code does.

| Example | The question it answers |
|---|---|
| `eda_1_signal_and_noise.py` | What does a Confirmed grade mean, and how often does pure noise earn one? |
| `eda_2_simpsons_paradox.py` | What happens when a difference between two kinds of author is really a difference between repos? |
| `eda_3_the_price_of_looking.py` | Why is the same answer worth less when it is the fortieth look than when it is the thirteenth? |

Run any of them from the repository root: `python3 examples/eda_1_signal_and_noise.py`. Example 3 runs the page's own `tools.js` in Node.
