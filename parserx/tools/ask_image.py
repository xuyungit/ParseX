"""``ask_image``: the agent asks the service VLM about an image (plan P2-5, guide §14 Q30 follow-up).

Vision through a tool: the agent's own model need not see images (it may be a text-only model, and reading
images in the main loop makes its cost hard to bound).  The agent names a block (its crop) or a page (the page
image) and asks a question; the service VLM answers from the image only.  The answer is document text — data, not
instructions (``DocText``).  Nothing in the workspace changes; the call is logged, and the image it read counts as
image evidence for a later ``correct`` of that block (or of any block on that page).

``questions`` asks several in one call: the requests run concurrently, the answers come back in the order asked,
and a problem with one question (unknown block, no image) is a failure of that question only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import model_validator

from parserx.ir.base import IRModel
from parserx.prompts import load_prompt
from parserx.scheduling import run_ordered
from parserx.tools.context import ToolContext, ToolOutput, output, service_failure
from parserx.tools.envelope import DocText, Failure, FailureCode, ToolFailure
from parserx.tools.read import ReadRequest, _image

PROMPT = "ask_image"


class Question(IRModel):
    block: str | None = None  # the block's image (its crop, or the figure / scan image itself)
    page: int | None = None  # the whole page image
    question: str

    @model_validator(mode="after")
    def _one_target(self) -> "Question":
        if (self.block is None) == (self.page is None):
            raise ValueError("give either block or page")
        if not self.question.strip():
            raise ValueError("ask a question")
        return self


class AskImageRequest(IRModel):
    block: str | None = None
    page: int | None = None
    question: str | None = None
    questions: list[Question] = []  # several questions in one call (P2-5); or block / page with question

    @model_validator(mode="after")
    def _one_form(self) -> "AskImageRequest":
        single = self.block is not None or self.page is not None or self.question is not None
        if single == bool(self.questions):
            raise ValueError("give block or page with a question, or a list of questions")
        if single:
            Question(block=self.block, page=self.page, question=self.question or "")
        return self


class AskAnswer(IRModel):
    block: str | None
    page: int | None
    answer: DocText | None
    image: str | None  # the image the answer was read from: pass it to correct as image evidence


class AskImageResult(IRModel):
    answer: DocText | None  # single question
    image: str | None  # single question
    answers: list[AskAnswer] = []  # in the order asked (questions that could be asked)


@dataclass
class _Task:
    question: Question
    image_path: Path
    image: str


def run(ctx: ToolContext, req: AskImageRequest) -> ToolOutput[AskImageResult]:
    single = not req.questions
    asked = [Question(block=req.block, page=req.page, question=req.question or "")] if single else req.questions
    state = ctx.ws.load()
    blocks = {b.id: b for b in state.blocks}
    pages = {p.n for p in state.pages}
    failures: list[Failure] = []
    tasks: list[_Task] = []
    for q in asked:
        target = q.block or f"p{q.page}"
        problem = None
        if q.block is not None and q.block not in blocks:
            problem = ToolFailure(FailureCode.NOT_FOUND, f"no block {q.block}", targets=[target])
        elif q.page is not None and q.page not in pages:
            problem = ToolFailure(FailureCode.NOT_FOUND, f"no page {q.page}", targets=[target])
        else:
            image, why = _image(ctx, state, ReadRequest(block=q.block, page=q.page,
                                                        image="crop" if q.block else "page"), blocks)
            if image is None:
                problem = ToolFailure(FailureCode.INVALID_REQUEST, why or "no image for this target", targets=[target])
        if problem is not None:
            if single:
                raise problem
            failures.append(problem.failure)
            continue
        tasks.append(_Task(q, Path(image.path), image.asset))

    prompt, _ = load_prompt(PROMPT)
    vlm = ctx.vlm(ctx.config.tools.ask_reasoning_effort)
    outcomes = run_ordered(tasks, lambda t: vlm.call(
        "describe_image", t.image_path, prompt, context=f"问题：{t.question.question}", temperature=0.0,
        max_tokens=ctx.config.tools.ask_max_tokens, structured_output_mode="off", json_schema_name="parserx_ask_image"),
        max_workers=ctx.config.services.vlm.max_concurrent)
    answers: list[AskAnswer] = []
    for outcome in outcomes:  # the order asked, whatever order the answers came in
        q = outcome.task.question
        if outcome.exception is not None:
            failures.append(service_failure(outcome.exception, [q.block or f"p{q.page}"]))
            text = None
        else:
            text = DocText(doc_text=str(outcome.value).strip())
        answers.append(AskAnswer(block=q.block, page=q.page, answer=text, image=outcome.task.image))
    first = answers[0] if single and answers else None
    return output(AskImageResult(answer=first.answer if first else None, image=first.image if first else None,
                                 answers=answers), failures=failures)
