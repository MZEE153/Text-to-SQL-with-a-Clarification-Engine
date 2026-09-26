"""Structural tests for the Step 9 graph (txt2sql_graph.py). The LLM steps
(classify, generate, format) are replaced by stubs, so nothing here uses API
quota and the outcomes are exact. The real database and the real Step 6/7
validator + read-only executor DO run, so the execute node is genuine.
What is being tested is the WIRING: routing, pause/resume, persistence.
"""
import os  # standard library: set an environment variable before LangGraph is imported

os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"  # strict mode: saving any unregistered custom class in a checkpoint now RAISES instead of warning, so a passing run proves the state holds only plain data

import tempfile  # standard library: a throwaway folder for the SQLite checkpoint file
from pathlib import Path  # standard library: path handling
from unittest.mock import patch  # temporarily replace a module attribute (the LLM steps) with a stub

from langgraph.checkpoint.sqlite import SqliteSaver  # the on-disk checkpointer (state survives the process)
from langgraph.types import Command  # how a paused graph is resumed with the user's answer

import txt2sql_graph as g  # the module under test (its nodes look up check_ambiguity etc. at call time, so patching works)
from ambiguity_classifier import AmbiguityCheck, Interpretation  # Step 4's result types, needed to build stub classifications
from answer_formatter import FinalAnswer  # Step 8's result type, needed to build a stub answer
from sql_generator import SQLGenerationResult  # Step 5's result type, needed to build a stub generation
from sql_validator import engine as admin_engine  # the admin engine, used only to read an independent expected value in section G
from sqlalchemy import text  # wrap a raw SQL string for execution

failures = 0  # running count of checks that did not go as expected


def check(label: str, ok: bool, detail: str = "") -> None:  # print one PASS/FAIL line and remember failures
    global failures  # we update the module-level counter
    if not ok:  # the expectation was not met
        failures += 1  # count it
    print(f"  [{'OK' if ok else '!! FAIL !!'}] {label}" + (f"  -- {detail}" if detail else ""))  # one line per check


CLEAR = AmbiguityCheck(is_ambiguous=False, reasoning="clear", assumed_default="Assuming completed orders only.")  # stub: a clear question with a stated default
AMBIGUOUS = AmbiguityCheck(  # stub: a genuinely ambiguous question with two interpretations
    is_ambiguous=True, reasoning="'best' has several meanings",
    interpretations=[Interpretation(label="Revenue", detail="DEF-REVENUE"), Interpretation(label="Orders", detail="DEF-ORDERS")],  # distinctive strings so we can see which one reaches the SQL generator
)
GOOD_SQL = "SELECT COUNT(*) FROM systems"  # a harmless query that the real validator and executor accept


class Calls:  # records what the stubs were called with, so tests can assert what did (and did not) run
    def __init__(self):  # start empty
        self.classify = []  # questions passed to the classifier
        self.generate = []  # resolved questions passed to the SQL generator
        self.format = []  # (assumption) values passed to the formatter


def stubs(calls: Calls, classification: AmbiguityCheck, sql: str = GOOD_SQL):  # patch all three LLM steps; returns the patchers to use in a with-block
    def fake_classify(question):  # replaces Step 4
        calls.classify.append(question)  # remember it was called
        return classification  # canned classification

    def fake_generate(resolved_question):  # replaces Step 5
        calls.generate.append(resolved_question)  # remember what it received
        return SQLGenerationResult(sql=sql, tables_used=["systems"], explanation="stub")  # canned SQL

    def fake_format(question, sql_text, result, assumption=None):  # replaces Step 8
        calls.format.append(assumption)  # remember the assumption it was given
        return FinalAnswer(text=f"ANSWER rows={result.rows}", used_llm=False, ungrounded_numbers=[])  # canned answer showing the real rows

    return (patch.object(g, "check_ambiguity", fake_classify), patch.object(g, "generate_sql", fake_generate), patch.object(g, "format_answer", fake_format))  # three patchers


def run(graph, inputs, config) -> list[str]:  # run (or resume) the graph and return the nodes that executed, in order
    return [node for chunk in graph.stream(inputs, config, stream_mode="updates") for node in chunk if node != "__interrupt__"]  # each chunk is {node_name: update}; the pause itself shows up as the special "__interrupt__" key, which we skip


def is_paused(graph, thread: str) -> bool:  # is this thread waiting for a user answer?
    return bool(graph.get_state(cfg(thread)).interrupts)  # NOT state.next: after a second pause on the same node, next reads () even though the thread is still waiting (probed and confirmed); interrupts stays populated


def is_finished(graph, thread: str) -> bool:  # has this thread run to completion?
    state = graph.get_state(cfg(thread))  # the saved snapshot
    return state.next == () and not state.tasks  # nothing left to run AND no task waiting (checking next alone would wrongly call a re-paused thread finished)


def open_graph(db_path):  # a fresh saver + graph on a given SQLite file
    saver_cm = SqliteSaver.from_conn_string(str(db_path))  # a context manager that opens the file
    saver = saver_cm.__enter__()  # open it
    return saver_cm, g.build_graph(saver)  # return the context manager (to close later) and the compiled graph


tmp = Path(tempfile.mkdtemp())  # a throwaway folder for the checkpoint files
cfg = lambda thread: {"configurable": {"thread_id": thread}}  # the per-conversation key the checkpointer stores state under

print("A. Clear question: no pause, straight through")  # section header
calls = Calls()  # fresh recorder
cm, graph = open_graph(tmp / "a.sqlite")  # open a graph
p = stubs(calls, CLEAR)  # the three patchers
with p[0], p[1], p[2]:  # patch the three LLM steps
    nodes = run(graph, {"question": "How many systems?"}, cfg("t-clear"))  # run to completion
final = graph.get_state(cfg("t-clear"))  # read the saved state
check("path skips ask_user", nodes == ["classify", "resolve", "generate", "execute", "format"], str(nodes))  # the exact route
check("graph finished (nothing left to run)", final.next == ())  # no pending nodes
check("assumption merged into the resolved question", "Assuming completed orders only." in final.values["resolved_question"])  # the default reached the SQL generator
check("assumption passed to the formatter", calls.format == ["Assuming completed orders only."])  # ...and to the answer stage
check("real executor ran the SQL", "ANSWER rows=[(150,)]" in final.values["answer"], final.values["answer"])  # 150 systems in the live database
cm.__exit__(None, None, None)  # close the file

print("B. Ambiguous question: pause, then resume")  # section header
calls = Calls()  # fresh recorder
cm, graph = open_graph(tmp / "b.sqlite")  # open a graph
p = stubs(calls, AMBIGUOUS)  # the three patchers
with p[0], p[1], p[2]:  # patch the LLM steps
    nodes1 = run(graph, {"question": "Who was the best customer?"}, cfg("t-amb"))  # first run: should stop at the pause
    paused = graph.get_state(cfg("t-amb"))  # inspect the saved, paused state
    check("first run stops after ask_user", nodes1 == ["classify"] and paused.next == ("ask_user",), f"ran={nodes1} next={paused.next}")  # classify ran; ask_user is pending
    payload = paused.tasks[0].interrupts[0].value  # what the pause handed to the caller
    check("pause payload carries both options", [o["label"] for o in payload["options"]] == ["Revenue", "Orders"], str(payload["options"]))  # the caller can show these to the user
    check("nothing downstream ran while paused", calls.generate == [] and calls.format == [])  # no SQL was generated before the user answered
    nodes2 = run(graph, Command(resume=1), cfg("t-amb"))  # resume with option 1 ("Orders")
    done = graph.get_state(cfg("t-amb"))  # state after resuming
check("resume continues from ask_user, not from the start", nodes2 == ["ask_user", "resolve", "generate", "execute", "format"], str(nodes2))  # classify does NOT run again
check("classifier was called exactly once in total", len(calls.classify) == 1, f"{len(calls.classify)} call(s)")  # no repeat LLM call on resume: the saved state was reused
check("chosen definition reached the SQL generator", "DEF-ORDERS" in calls.generate[0] and "DEF-REVENUE" not in calls.generate[0], calls.generate[0])  # option 1 chosen, option 0 absent
check("chosen definition shown with the answer", calls.format == ["DEF-ORDERS"])  # passed to the formatter as the stated assumption
check("graph finished", done.next == ())  # nothing left to run
cm.__exit__(None, None, None)  # close the file

print("C. Bad answers: the thread stays cleanly paused (no crash, no dead thread)")  # section header
calls = Calls()  # fresh recorder
cm, graph = open_graph(tmp / "c.sqlite")  # open a graph
p = stubs(calls, AMBIGUOUS)  # the three patchers
with p[0], p[1], p[2]:  # patch the LLM steps
    run(graph, {"question": "Who was the best customer?"}, cfg("t-free"))  # pause
    run(graph, Command(resume=9), cfg("t-free"))  # answer with an out-of-range option number: must NOT raise
    st = graph.get_state(cfg("t-free"))  # the state after the bad answer
    check("out-of-range number: still paused, waiting for a valid answer", is_paused(graph, "t-free") and not is_finished(graph, "t-free"), f"interrupts={len(st.interrupts)}")  # the thread is alive and waiting (an earlier version that raised left a dead-looking thread)
    reask = st.tasks[0].interrupts[-1].value  # the payload of the newest pause
    check("user is re-asked with an error and the same options", "not a valid choice" in reask.get("error", "") and len(reask["options"]) == 2, reask.get("error", "")[:70])  # clear message plus the options again
    run(graph, Command(resume="   "), cfg("t-free"))  # answer with blank text: also invalid
    check("blank text also refused, still paused", is_paused(graph, "t-free"))  # same handling
    nodes_ok = run(graph, Command(resume="my own definition"), cfg("t-free"))  # finally a valid free-text answer
check("a valid answer after two bad ones completes the run", nodes_ok[-1] == "format" and is_finished(graph, "t-free") and not is_paused(graph, "t-free"), str(nodes_ok))  # the thread was not poisoned by the bad answers
check("free-text answer reached the SQL generator verbatim", "my own definition" in calls.generate[0], calls.generate[0])  # used as given
check("classifier still called only once", len(calls.classify) == 1, f"{len(calls.classify)} call(s)")  # three resumes, zero repeat LLM calls
cm.__exit__(None, None, None)  # close the file

print("D. Refusal path: unsafe SQL never reaches the formatter")  # section header
calls = Calls()  # fresh recorder
cm, graph = open_graph(tmp / "d.sqlite")  # open a graph
p = stubs(calls, CLEAR, sql="DELETE FROM customers")  # the stub generator 'misbehaves' and writes a DELETE
with p[0], p[1], p[2]:  # patch the LLM steps
    nodes = run(graph, {"question": "Remove all customers"}, cfg("t-fail"))  # run
answer = graph.get_state(cfg("t-fail")).values["answer"]  # the final text
check("routed to fail, not format", nodes == ["classify", "resolve", "generate", "execute", "fail"], str(nodes))  # the exact route
check("formatter never called", calls.format == [])  # no answer was invented
check("user is told plainly", answer.startswith("I could not produce a safe, runnable query") and "SQLValidationError" in answer, answer.splitlines()[0])  # clear refusal message
cm.__exit__(None, None, None)  # close the file

print("E. Persistence: resume from a brand-new graph and connection")  # section header
db = tmp / "e.sqlite"  # one checkpoint file shared by both sessions
calls = Calls()  # fresh recorder
cm, graph = open_graph(db)  # SESSION 1
p = stubs(calls, AMBIGUOUS)  # the three patchers
with p[0], p[1], p[2]:  # patch the LLM steps
    run(graph, {"question": "Who was the best customer?"}, cfg("t-persist"))  # run to the pause
cm.__exit__(None, None, None)  # close the connection and throw the graph away
del graph  # nothing from session 1 stays in memory
check("checkpoint file exists on disk", db.exists() and db.stat().st_size > 0, f"{db.stat().st_size} bytes")  # the state lives in the file
cm, graph2 = open_graph(db)  # SESSION 2: everything new (connection, saver, compiled graph)
p = stubs(calls, AMBIGUOUS)  # patch again (still the same recorder)
with p[0], p[1], p[2]:  # patch the LLM steps
    check("new session sees the paused thread", graph2.get_state(cfg("t-persist")).next == ("ask_user",))  # found purely from the file
    nodes = run(graph2, Command(resume=0), cfg("t-persist"))  # resume with option 0 ("Revenue")
check("resumed to completion without re-classifying", nodes[0] == "ask_user" and nodes[-1] == "format" and len(calls.classify) == 1, f"{nodes}; classify calls={len(calls.classify)}")  # classify not repeated
check("state survived intact (chosen definition used)", "DEF-REVENUE" in calls.generate[0])  # the saved classification's options were still there
cm.__exit__(None, None, None)  # close the file

print("F. Threads are isolated")  # section header
db = tmp / "f.sqlite"  # one checkpoint file, two conversations
calls = Calls()  # fresh recorder
cm, graph = open_graph(db)  # open a graph
p = stubs(calls, AMBIGUOUS)  # the three patchers
with p[0], p[1], p[2]:  # patch the LLM steps
    run(graph, {"question": "Q for thread one"}, cfg("one"))  # pause thread one
    run(graph, {"question": "Q for thread two"}, cfg("two"))  # pause thread two
    run(graph, Command(resume=0), cfg("two"))  # resume ONLY thread two
check("thread two finished, thread one still paused", graph.get_state(cfg("two")).next == () and graph.get_state(cfg("one")).next == ("ask_user",))  # answering one conversation does not touch the other
check("each thread kept its own question", graph.get_state(cfg("one")).values["question"] == "Q for thread one" and graph.get_state(cfg("two")).values["question"] == "Q for thread two")  # no cross-talk
cm.__exit__(None, None, None)  # close the file

print("G. Decimal and date values survive the checkpoint (strict serializer mode)")  # section header
db = tmp / "g.sqlite"  # one checkpoint file, two sessions
calls = Calls()  # fresh recorder
typed_sql = "SELECT name, created_at, (SELECT SUM(total_amount) FROM orders) AS total FROM customers ORDER BY customer_id LIMIT 2"  # returns a str, a date and a Decimal per row
cm, graph = open_graph(db)  # SESSION 1
p = stubs(calls, CLEAR, sql=typed_sql)  # the stub generator returns that SQL
with p[0], p[1], p[2]:  # patch the LLM steps
    run(graph, {"question": "typed values"}, cfg("t-types"))  # run to completion (the real executor produces real rows)
cm.__exit__(None, None, None)  # close the connection
cm, graph2 = open_graph(db)  # SESSION 2: read the saved state back from the file
first_row = graph2.get_state(cfg("t-types")).values["result"]["rows"][0]  # the first saved row
with admin_engine.connect() as admin:  # ask the database directly, as an independent source of truth
    expected_total = admin.execute(text("SELECT SUM(total_amount) FROM orders")).scalar_one()  # the Decimal the query should have produced (all orders, cancelled included)
check("saved rows equal what the database says", first_row[0] == "Acme Corp" and str(first_row[1]) == "2023-01-15" and first_row[2] == expected_total, repr(first_row))  # same values that went in
check("date and Decimal keep their types (strict mode, no custom classes)", [type(v).__name__ for v in first_row] == ["str", "date", "Decimal"], str([type(v).__name__ for v in first_row]))  # exact types survive the round trip
cm.__exit__(None, None, None)  # close the file

print()  # blank line before the verdict
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")  # overall verdict
