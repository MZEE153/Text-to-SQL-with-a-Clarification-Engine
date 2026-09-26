"""Step 9: wire Steps 4-8 into one LangGraph workflow.

    START -> classify --(ambiguous)--> ask_user --+
                 |                                |   ask_user PAUSES the graph with interrupt();
                 +--(clear / default)-------------+   the state is saved by the checkpointer and
                                                  v   the run can be resumed later, even from
                                               resolve   a different process
                                                  v
                                               generate
                                                  v
                                               execute --(error)--> fail --> END
                                                  |
                                               (ok) v
                                               format --> END

Every LLM step is one node; the branch decisions are plain Python functions
that read typed state, which is what the Pydantic structured outputs of
Steps 4-8 were for.
"""
from dataclasses import asdict  # standard library: turn a dataclass (QueryResult) into a plain dict
from typing import TypedDict  # standard library: describes the shape (keys and value types) of the graph's shared state

from langgraph.graph import StateGraph, START, END  # StateGraph = the workflow builder; START / END = the entry and exit markers
from langgraph.types import interrupt  # interrupt() pauses the graph and hands a payload to the caller

from ambiguity_classifier_groq import check_ambiguity  # Step 4 (Groq): question -> AmbiguityCheck
from sql_generator_groq import generate_sql  # Step 5 (Groq): resolved question -> generated SQL
from sql_validator import SQLValidationError  # Step 6's error type, caught in the execute node
from sql_executor import execute_sql, SQLExecutionError, QueryResult  # Step 7: validate + run under the read-only role; its error type and result type
from answer_formatter import format_answer  # Step 8: rows -> plain-English answer


class GraphState(TypedDict, total=False):  # the shared state every node reads from and writes to; total=False = keys appear as the run progresses
    # Only plain data (str / dict / list / numbers) is kept in state on purpose: LangGraph warned that saving custom
    # classes (Pydantic models, dataclasses) in a checkpoint "will be blocked in a future version".
    question: str  # the user's original question
    check: dict  # Step 4's AmbiguityCheck, stored as a plain dict (model_dump)
    assumed_default: str | None  # the default Step 4 assumed for a clear-but-underspecified question
    clarification: str  # the interpretation the user chose (or typed) after being asked
    resolved_question: str  # the question with any clarification / assumption merged in; this is what the SQL generator receives
    sql: str  # the generated SQL
    result: dict  # the executor's QueryResult, stored as a plain dict (columns, rows, truncated, elapsed_ms)
    error: str  # why validation or execution refused, if it did
    answer: str  # the final user-facing text


def classify(state: GraphState) -> dict:  # node 1: is the question clear, defaultable, or genuinely ambiguous?
    check = check_ambiguity(state["question"])  # one LLM call (Step 4)
    return {"check": check.model_dump(), "assumed_default": check.assumed_default}  # store the classification as a plain dict, and the assumption separately so later nodes can read it directly


def route_after_classify(state: GraphState) -> str:  # branch: decides which node runs next
    return "ask_user" if state["check"]["is_ambiguous"] else "resolve"  # ambiguous -> ask the user; otherwise carry straight on


def _is_valid_reply(reply, option_count: int) -> bool:  # is this a usable answer to the clarification question?
    if isinstance(reply, int) and not isinstance(reply, bool):  # a number picks one of the options
        return 0 <= reply < option_count  # it must be inside the list
    return isinstance(reply, str) and reply.strip() != ""  # otherwise it must be non-empty free text


def ask_user(state: GraphState) -> dict:  # node 2 (ambiguous branch only): pause and let the user choose an interpretation
    options = [{"label": o["label"], "detail": o["detail"]} for o in state["check"]["interpretations"]]  # the candidate interpretations, as plain dicts
    payload = {"question": state["question"], "reasoning": state["check"]["reasoning"], "options": options}  # what the caller is shown while the graph is paused
    reply = interrupt(payload)  # PAUSES here. First run: stops the graph and hands `payload` to the caller. On resume this node re-runs from the top and interrupt() returns the value the user supplied
    while not _is_valid_reply(reply, len(options)):  # bad input must NOT raise: an exception after a resume left the thread looking finished (next=()) with no answer, and every later resume replayed the stored bad value
        reply = interrupt({**payload, "error": f"{reply!r} is not a valid choice. Enter a number from 0 to {len(options) - 1}, or type your own definition."})  # instead, pause again with an error message; resume values are consumed in order, so the next answer lands here
    if isinstance(reply, int):  # the user picked an option by number
        return {"clarification": options[reply]["detail"]}  # use that option's precise definition
    return {"clarification": reply.strip()}  # otherwise use the user's own free-text definition


def resolve(state: GraphState) -> dict:  # node 3: merge whatever was settled into one unambiguous question
    question = state["question"]  # the original wording
    if state.get("clarification"):  # the user chose or typed an interpretation
        resolved = f"{question} Interpret it as follows: {state['clarification']}"  # append it, so the SQL generator gets an explicit definition
    elif state.get("assumed_default"):  # Step 4 assumed a default
        resolved = f"{question} {state['assumed_default']}"  # append the assumption so the SQL follows it
    else:  # nothing to settle
        resolved = question  # the question was already clear
    return {"resolved_question": resolved}  # this is what generate_sql receives


def generate(state: GraphState) -> dict:  # node 4: resolved question -> SQL
    return {"sql": generate_sql(state["resolved_question"]).sql}  # one LLM call (Step 5); keep only the SQL text


def execute(state: GraphState) -> dict:  # node 5: validate and run the SQL
    try:  # the validator or the database can still refuse
        return {"result": asdict(execute_sql(state["sql"]))}  # Steps 6 + 7: validate, then run as the read-only role; stored as a plain dict
    except (SQLValidationError, SQLExecutionError) as e:  # refused before or during execution
        return {"error": f"{type(e).__name__}: {e}"}  # record why, and let the router send us to the fail node


def route_after_execute(state: GraphState) -> str:  # branch: did execution succeed?
    return "fail" if state.get("error") else "format"  # an error goes to the fail node, success to formatting


def format_result(state: GraphState) -> dict:  # node 6: rows -> plain-English answer
    assumption = state.get("clarification") or state.get("assumed_default")  # whichever was settled is shown to the user with the answer
    final = format_answer(state["question"], state["sql"], QueryResult(**state["result"]), assumption)  # Step 8; the dict is rebuilt into a QueryResult, and the ORIGINAL question is passed so the model words the answer to what the user actually asked
    return {"answer": final.text}  # the complete text, including any appended assumption / cut-off / warning lines


def fail(state: GraphState) -> dict:  # node 7: the safe way out when no query could be run
    return {"answer": f"I could not produce a safe, runnable query for that question.\n\nReason: {state['error']}"}  # say so plainly instead of guessing an answer


def build_graph(checkpointer):  # assemble and compile the workflow; the checkpointer is what makes pause/resume possible
    builder = StateGraph(GraphState)  # a new graph whose shared state has the shape above
    builder.add_node("classify", classify)  # register each function as a named node
    builder.add_node("ask_user", ask_user)  # ... the pause point
    builder.add_node("resolve", resolve)  # ... merge clarification / assumption
    builder.add_node("generate", generate)  # ... SQL generation
    builder.add_node("execute", execute)  # ... validate + run
    builder.add_node("format", format_result)  # ... word the answer
    builder.add_node("fail", fail)  # ... the error exit
    builder.add_edge(START, "classify")  # every run begins by classifying the question
    builder.add_conditional_edges("classify", route_after_classify, ["ask_user", "resolve"])  # branch 1: ambiguous -> ask_user, otherwise -> resolve
    builder.add_edge("ask_user", "resolve")  # after the user answers, resolve the question
    builder.add_edge("resolve", "generate")  # then generate SQL
    builder.add_edge("generate", "execute")  # then run it
    builder.add_conditional_edges("execute", route_after_execute, ["format", "fail"])  # branch 2: success -> format, refusal -> fail
    builder.add_edge("format", END)  # done
    builder.add_edge("fail", END)  # done
    return builder.compile(checkpointer=checkpointer)  # compile with the checkpointer so state is saved after every node
