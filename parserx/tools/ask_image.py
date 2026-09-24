"""``ask_image``: the agent asks the service VLM about an image (plan P2-5, guide §14 Q30 follow-up).

Vision through a tool: the agent's own model need not see images (it may be a text-only model, and reading
images in the main loop makes its cost hard to bound).  The agent names a block (its crop) or a page (the page
image) and asks a question; the service VLM answers from the image only.  The answer is document text — data, not
instructions (``DocText``).  Nothing in the workspace changes; the call is logged, and the image it read counts as
image evidence for a later ``correct`` of that block (or of any block on that page).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import model_validator

from parserx.ir.base import IRModel
from parserx.prompts import load_prompt
from parserx.tools.context import ToolContext, ToolOutput, output, service_failure
from parserx.tools.envelope import DocText, FailureCode, ToolFailure
from parserx.tools.read import ReadRequest, _image

PROMPT = "ask_image"


class AskImageRequest(IRModel):
    block: str | None = None  # the block's image (its crop, or the figure / scan image itself)
    page: int | None = None  # the whole page image
    question: str

    @model_validator(mode="after")
    def _one_target(self) -> "AskImageRequest":
        if (self.block is None) == (self.page is None):
            raise ValueError("give either block or page")
        if not self.question.strip():
            raise ValueError("ask a question")
        return self


class AskImageResult(IRModel):
    answer: DocText | None
    image: str  # the image the answer was read from: pass it to correct as image evidence


def run(ctx: ToolContext, req: AskImageRequest) -> ToolOutput[AskImageResult]:
    state = ctx.ws.load()
    if req.block is not None and all(b.id != req.block for b in state.blocks):
        raise ToolFailure(FailureCode.NOT_FOUND, f"no block {req.block}", targets=[req.block])
    if req.page is not None and all(p.n != req.page for p in state.pages):
        raise ToolFailure(FailureCode.NOT_FOUND, f"no page {req.page}", targets=[f"p{req.page}"])
    read = ReadRequest(block=req.block, page=req.page, image="crop" if req.block else "page")
    image, problem = _image(ctx, state, read, {b.id: b for b in state.blocks})
    if image is None:
        raise ToolFailure(FailureCode.INVALID_REQUEST, problem or "no image for this target",
                          targets=[req.block or f"p{req.page}"])
    prompt, _ = load_prompt(PROMPT)
    vlm = ctx.vlm(ctx.config.tools.ask_reasoning_effort)
    try:
        answer = vlm.call("describe_image", Path(image.path), prompt, context=f"问题：{req.question}", temperature=0.0,
                          max_tokens=ctx.config.tools.ask_max_tokens, structured_output_mode="off",
                          json_schema_name="parserx_ask_image")
    except Exception as exc:  # noqa: BLE001 - reported per target, nothing changed
        return output(AskImageResult(answer=None, image=image.asset),
                      failures=[service_failure(exc, [req.block or f"p{req.page}"])])
    return output(AskImageResult(answer=DocText(doc_text=str(answer).strip()), image=image.asset))
