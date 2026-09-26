"""Live end-to-end test of Step 9: the real CLI, real Groq models, real database.
Every `ask` and every `answer` is its own PROCESS, so a pause in one process is
genuinely resumed in another, from the SQLite checkpoint file only.

The payoff being checked: "Who was the best customer last year?" must give a
DIFFERENT correct answer depending on which interpretation the user picks --
revenue -> Acme Corp, order count -> Globex Inc, profit -> Stark Industries.
"""
import re  # standard library: pull the numbered options out of the CLI's text output
import subprocess  # standard library: run the CLI as a separate process
import sys  # standard library: sys.executable = the venv's Python
import tempfile  # standard library: a throwaway folder for the checkpoint file
import uuid  # standard library: unique thread ids
from pathlib import Path  # standard library: path handling

HERE = Path(__file__).resolve().parent  # the project folder
DB = str(Path(tempfile.mkdtemp()) / "live.sqlite")  # a throwaway checkpoint file, so this test never touches the real one

failures = 0  # running count of checks that did not go as expected


def check(label: str, ok: bool, detail: str = "") -> None:  # print one PASS/FAIL line and remember failures
    global failures  # we update the module-level counter
    if not ok:  # the expectation was not met
        failures += 1  # count it
    print(f"  [{'OK' if ok else '!! FAIL !!'}] {label}" + (f"  -- {detail}" if detail else ""))  # one line per check


def cli(*args: str) -> str:  # run `python run_graph.py --db <tmp> <args>` as a separate process and return its output
    proc = subprocess.run([sys.executable, str(HERE / "run_graph.py"), "--db", DB, *args], capture_output=True, text=True, timeout=240, cwd=HERE)  # a brand-new process every time
    return proc.stdout + ("\n[stderr]\n" + proc.stderr if proc.returncode != 0 else "")  # stdout normally; stderr too if the process failed


def options_of(output: str) -> list[tuple[int, str]]:  # parse "  [0] Label: definition" lines into (number, text) pairs
    return [(int(n), text) for n, text in re.findall(r"^\s*\[(\d+)\] (.*)$", output, flags=re.M)]  # one pair per option line


def pick(options: list[tuple[int, str]], want: str) -> int | None:  # find the option number whose LABEL matches a metric
    for number, text in options:  # go through the offered options
        label = text.split(":")[0].lower()  # the label is the part before the first colon
        if want == "revenue" and ("revenue" in label or "sales" in label):  # revenue may be labelled "total sales"
            return number  # found it
        if want == "orders" and "order" in label and "average" not in label and "value" not in label:  # order COUNT, not average order value
            return number  # found it
        if want == "profit" and "profit" in label and "margin" not in label:  # total profit
            return number  # found it
    return None  # this run's classifier did not offer that metric


print("1. A clear question runs straight through (one process, no pause)")  # section header
out = cli("ask", "How many systems signed off last year?")  # ask
print("      " + out.replace("\n", "\n      ").rstrip())  # show the real output
check("answered directly, no clarification", "ANSWER:" in out and "CLARIFICATION NEEDED" not in out)  # no pause
check("correct count (12) in the answer", re.search(r"\b12\b", out.split("ANSWER:")[-1].split("SQL used:")[0]) is not None)  # 12 systems signed off in 2025

print("2. The ambiguous question: pause in one process, resume in another, three ways")  # section header
expected = {  # the three ground-truth winners for 2025, AND their figures (the first version of this test checked names only, and passed while printing wrong figures)
    "revenue": ("Acme Corp", r"50000\.01"),  # completed-orders revenue; counting the cancelled $8,000 order would give 58000.01
    "orders": ("Globex Inc", r"\b12\b"),  # 12 completed orders; counting the cancelled one would give 13
    "profit": ("Stark Industries", r"27999\.96"),  # profit from completed orders
}
for metric, (winner, figure) in expected.items():  # once per interpretation
    thread = f"live-{metric}-{uuid.uuid4().hex[:4]}"  # a distinct conversation each time
    first = cli("ask", "Who was the best customer last year?", "--thread", thread)  # PROCESS 1: ask (should pause and exit)
    paused = "CLARIFICATION NEEDED" in first  # did it ask?
    check(f"[{metric}] pipeline paused and asked", paused)  # the question is genuinely ambiguous
    if not paused:  # nothing to resume
        print("      " + first.replace("\n", "\n      ").rstrip())  # show what happened instead
        continue  # next metric
    opts = options_of(first)  # the offered interpretations
    choice = pick(opts, metric)  # the option matching this metric
    if choice is None:  # this run's classifier did not offer it
        check(f"[{metric}] option offered", False, f"offered: {[t.split(':')[0] for _, t in opts]}")  # report it honestly
        continue  # next metric
    second = cli("answer", "--thread", thread, str(choice))  # PROCESS 2: a different process resumes from the file
    print(f"      [{metric}] chose option {choice}: {dict(opts)[choice][:90]}")  # what was chosen
    print("      " + second.replace("\n", "\n      ").rstrip())  # the real final output
    answer_part = second.split("ANSWER:")[-1].split("SQL used:")[0]  # the answer text only (not the SQL, which could contain digits)
    check(f"[{metric}] resumed in a new process and got {winner}", "ANSWER:" in second and winner in answer_part)  # the winner for that definition
    check(f"[{metric}] the FIGURE is right too", re.search(figure, answer_part) is not None, f"expected /{figure}/ in: {answer_part.strip().splitlines()[0] if answer_part.strip() else ''}")  # the number must match ground truth, not just the name

print("3. A question whose default may or may not trigger a pause")  # section header
out = cli("ask", "What is the total sales in all years?")  # ask
print("      " + out.replace("\n", "\n      ").rstrip())  # show the real output
check("either answered or asked (no crash)", "ANSWER:" in out or "CLARIFICATION NEEDED" in out)  # the pipeline handled it either way
if "ANSWER:" in out:  # it answered directly
    check("total is completed-orders-only: 3077465.82", "3077465.82" in out.replace(",", ""), "if this fails, cancelled orders were included")  # the ground truth for 'sales'

print("4. Answering a thread that isn't waiting")  # section header
out = cli("answer", "--thread", "does-not-exist", "0")  # nothing is paused on that id
check("clear message, no crash", "Nothing is waiting" in out, out.strip().splitlines()[0] if out.strip() else "")  # graceful handling

print()  # blank line before the verdict
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")  # overall verdict
