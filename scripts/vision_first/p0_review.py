"""The P0 review page (execution plan §3.3): one self-contained HTML file to check each page against the original.

Per page: the page image with every text-layer line coloured by where the chosen configuration put it (copy, write,
table, aside excluded, aside into a block; repaired lines outlined) and its regions (figures, written content
without lines); the configuration's Markdown with its differences from M's page marked; the annotation's, M's, R's
and L's pages; the written parts with the comparison's signals; the page scores.

    uv run python scripts/vision_first/p0_review.py --run-dir eval_runs/<run>   # → <run-dir>/review/index.html
"""

from __future__ import annotations

import argparse
import base64
import difflib
import html
import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image  # noqa: E402

from p0_inputs import PAGES, load, page_id  # noqa: E402
from p0_run import CONFIGS  # noqa: E402
from p0_score import ARMS, Pages  # noqa: E402


def _jpeg(path: Path) -> str:
    with Image.open(path) as image:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, "JPEG", quality=72)
    return base64.b64encode(buf.getvalue()).decode()


def diff_html(ours: str, theirs: str) -> str:
    """*ours* with what *theirs* (M) lacks marked <ins>, and what it has that ours lacks <del>."""
    matcher = difflib.SequenceMatcher(None, theirs, ours, autojunk=False)
    out = []
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "equal":
            out.append(html.escape(ours[b0:b1]))
        else:
            if a1 > a0:
                out.append(f"<del>{html.escape(theirs[a0:a1])}</del>")
            if b1 > b0:
                out.append(f"<ins>{html.escape(ours[b0:b1])}</ins>")
    return "".join(out)


def build(run_dir: Path) -> Path:
    scores = json.loads((run_dir / "scores.json").read_text(encoding="utf-8"))
    pages_of = Pages()
    names = [c[0] for c in CONFIGS]
    data = {"configs": [], "pages": []}
    for doc, n in PAGES:
        pid = page_id(doc, n)
        page = load(run_dir, pid)
        info = pages_of.of(doc)
        m_page = info["arms"].get("M", [""] * n)[n - 1]
        entry = {"id": pid, "image": _jpeg(run_dir / "inputs" / page["image"]["file"]),
                 "width": page["image"]["width"], "height": page["image"]["height"],
                 "lines": [{"id": l["id"], "box": l["box"], "text": l["text"]} for l in page["lines"]],
                 "expected": info["expected"][n - 1],
                 "arms": {a: s[n - 1] for a, s in info["arms"].items()},
                 "arm_scores": scores["pages"][pid]["arms"], "configs": {}}
        for name in names:
            path = run_dir / "results" / name / f"{pid}.json"
            if not path.exists():
                continue
            result = json.loads(path.read_text(encoding="utf-8"))
            final = result["final"]["data"]
            repaired = {p for b in final["blocks"] if b.get("repaired") for part in b["parts"] for p in part["lines"]}
            regions = [{"id": b["id"], "type": b["type"], "box": b["region"]} for b in final["blocks"] if b.get("region")]
            entry["configs"][name] = {
                "status": result["final"]["status"], "destinations": result["destinations"],
                "repaired": sorted(repaired), "regions": regions, "markdown": result["markdown"],
                "diff": diff_html(result["markdown"], m_page), "score": scores["pages"][pid]["configs"][name],
                "aside": final.get("aside", []), "unresolved": final.get("unresolved", []),
                "repairs": result["final"]["repairs"],
            }
            if name not in data["configs"]:
                data["configs"].append(name)
        data["pages"].append(entry)
    out = run_dir / "review" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False)).replace("__ARMS__", json.dumps(list(ARMS))),
                   encoding="utf-8")
    return out


PAGE = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>P0 page review</title>
<style>
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#deded8;--copy:#2e9e5b;--write:#e0831a;--table:#2f6fd6;
--excl:#8d8d88;--block:#8a4fd1;--bad:#d4312a;--ins:#d7f5df;--del:#fbd9d6}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#171716;--fg:#ecece8;--muted:#a3a39c;--card:#22221f;
--line:#3a3a36;--ins:#1f4a2c;--del:#5a2320}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px}
header h1{font-size:16px;margin:0 0 6px}
.btns button{margin:2px;padding:4px 10px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;cursor:pointer}
.btns button.on{background:var(--fg);color:var(--bg)}
.legend span{display:inline-block;margin-right:12px;font-size:12px}
.legend i{display:inline-block;width:12px;height:12px;margin-right:4px;vertical-align:-2px;border-radius:2px}
section{margin:16px;padding:12px;background:var(--card);border:1px solid var(--line);border-radius:10px}
section h2{font-size:15px;margin:0 0 8px}
.grid{display:grid;grid-template-columns:minmax(280px,1fr) minmax(280px,1fr) minmax(280px,1fr);gap:12px}
@media (max-width:1100px){.grid{grid-template-columns:1fr}}
.imgwrap{position:relative;width:100%}
.imgwrap img{width:100%;display:block}
.imgwrap svg{position:absolute;inset:0;width:100%;height:100%}
pre{white-space:pre-wrap;word-break:break-word;font:12px/1.45 ui-monospace,Menlo,monospace;background:var(--bg);padding:8px;
border-radius:6px;max-height:900px;overflow:auto;margin:0}
ins{background:var(--ins);text-decoration:none}del{background:var(--del)}
.tabs button{font-size:12px;margin:0 4px 4px 0;padding:2px 8px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:5px}
.tabs button.on{background:var(--fg);color:var(--bg)}
table{border-collapse:collapse;font-size:12px;margin-top:6px}td,th{border:1px solid var(--line);padding:2px 6px;text-align:right}
th:first-child,td:first-child{text-align:left}
.meta{color:var(--muted);font-size:12px}
#tip{position:fixed;pointer-events:none;background:var(--fg);color:var(--bg);font-size:12px;padding:3px 6px;border-radius:4px;display:none;max-width:420px;z-index:9}
</style></head><body>
<header><h1>P0 page review — allocation by configuration</h1>
<div class="btns" id="cfg"></div>
<div class="legend"><span><i style="background:var(--copy)"></i>copy</span><span><i style="background:var(--write)"></i>write</span>
<span><i style="background:var(--table)"></i>table</span><span><i style="background:var(--excl)"></i>aside: excluded</span>
<span><i style="background:var(--block)"></i>aside: into a block</span><span><i style="border:2px solid var(--bad)"></i>put back by the program / not allocated</span>
<span><i style="border:2px dashed var(--block)"></i>region (figure, written content without lines)</span>
<span class="meta">Middle column: the configuration's Markdown; <ins>green</ins> not in M's page, <del>red</del> in M's page only.</span></div>
</header>
<div id="pages"></div><div id="tip"></div>
<script>
const DATA=__DATA__, ARMS=__ARMS__;
let cfg=DATA.configs[0];
const colour={copy:'var(--copy)',write:'var(--write)',table:'var(--table)','aside:excluded':'var(--excl)','aside:block':'var(--block)'};
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function kind(d){if(!d)return null;const [k,to]=d.split(':');return k==='aside'?(to==='excluded'?'aside:excluded':'aside:block'):k}
function renderButtons(){const el=document.getElementById('cfg');el.innerHTML='';
 DATA.configs.forEach(c=>{const b=document.createElement('button');b.textContent=c;b.className=c===cfg?'on':'';b.onclick=()=>{cfg=c;renderButtons();renderPages()};el.appendChild(b)})}
function scoreRow(label,s){if(!s)return '';return `<tr><td>${label}</td><td>${s.key_errors}</td><td>${(s.char_f1??0).toFixed(3)}</td><td>${s.key_missing.script}/${s.key_extra.script}</td><td>${s.key_missing.number}/${s.key_extra.number}</td><td>${s.key_missing.unmapped}/${s.key_extra.unmapped}</td><td>${s.lost_runs} (${s.lost_chars})</td><td>${s.added_runs} (${s.added_chars})</td><td>${s.formulas_paired??'—'}/${s.formulas_expected??'—'}</td><td>${s.formula_similarity==null?'—':s.formula_similarity.toFixed(3)}</td></tr>`}
function renderPages(){const root=document.getElementById('pages');root.innerHTML='';
 DATA.pages.forEach(p=>{const c=p.configs[cfg];const sec=document.createElement('section');
  let svg=`<svg viewBox="0 0 ${p.width} ${p.height}" preserveAspectRatio="none">`;
  if(c){const rep=new Set(c.repaired);
   p.lines.forEach(l=>{const d=c.destinations[l.id];const k=kind(d);const b=l.box;const col=k?colour[k]:'var(--bad)';
    svg+=`<rect x="${b[0]}" y="${b[1]}" width="${Math.max(1,b[2]-b[0])}" height="${Math.max(1,b[3]-b[1])}" fill="${col}" fill-opacity="0.28" stroke="${rep.has(l.id)||!k?'var(--bad)':col}" stroke-width="${rep.has(l.id)||!k?3:0.8}" data-tip="${esc(l.id+' → '+(d||'NOT ALLOCATED')+' · '+l.text)}"/>`});
   c.regions.forEach(r=>{const b=r.box;svg+=`<rect x="${b[0]}" y="${b[1]}" width="${b[2]-b[0]}" height="${b[3]-b[1]}" fill="none" stroke="var(--block)" stroke-width="3" stroke-dasharray="10 6" data-tip="${esc(r.id+' '+r.type+' region')}"/>`});}
  svg+='</svg>';
  const tabs=['annotation',...ARMS.filter(a=>p.arms[a]!==undefined)];
  const body=c?`<div class="meta">${esc(cfg)} · ${esc(c.status)}${c.repairs.length?' · repairs: '+esc(c.repairs.join('；')):''}</div><pre>${c.diff}</pre>`:'<div class="meta">no result</div>';
  let written='';
  if(c){const rows=c.score.written.map(w=>`<tr><td>${w.block} ${w.type}</td><td>${w.lines}</td><td style="text-align:left">${esc(w.text)}</td><td style="text-align:left">${w.lines?esc(JSON.stringify(w.numbers_missing))+' / '+esc(JSON.stringify(w.numbers_added)):'unseen: '+esc(JSON.stringify(w.numbers_unseen))}</td><td style="text-align:left">${w.lines?esc(w.chars_missing)+' / '+esc(w.chars_added):esc(w.chars_unseen)}</td></tr>`).join('');
   written=`<details><summary>written parts (${c.score.written.length}) — numbers and letters missing / added against the replaced lines</summary><table><tr><th>block</th><th>lines</th><th>text</th><th>numbers −/+</th><th>letters −/+</th></tr>${rows}</table></details>`+
   (c.aside.length?`<details><summary>aside (${c.aside.length})</summary><pre>${esc(c.aside.map(a=>a.lines.join(',')+' → '+a.to+' ('+a.reason+')').join('\n'))}</pre></details>`:'')+
   (c.unresolved.length?`<details open><summary>unresolved (${c.unresolved.length})</summary><pre>${esc(c.unresolved.map(u=>u.where+': '+u.what).join('\n'))}</pre></details>`:'');}
  const scoreTable=`<table><tr><th>arm</th><th>key errors</th><th>char_f1</th><th>script −/+</th><th>number −/+</th><th>unmapped −/+</th><th>lost runs</th><th>added runs</th><th>formulas</th><th>formula sim</th></tr>${ARMS.map(a=>scoreRow(a,p.arm_scores[a])).join('')}${c?scoreRow(cfg,c.score.score):''}</table>`;
  sec.innerHTML=`<h2>${esc(p.id)}</h2>${scoreTable}${written}<div class="grid" style="margin-top:8px"><div class="imgwrap"><img src="data:image/jpeg;base64,${p.image}">${svg}</div><div>${body}</div><div><div class="tabs">${tabs.map((t,i)=>`<button data-t="${t}" class="${i?'':'on'}">${t==='annotation'?'annotation':t}</button>`).join('')}</div><pre class="other">${esc(p.expected)}</pre></div></div>`;
  sec.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{sec.querySelectorAll('.tabs button').forEach(x=>x.className='');b.className='on';sec.querySelector('pre.other').textContent=b.dataset.t==='annotation'?p.expected:p.arms[b.dataset.t]});
  root.appendChild(sec)});}
const tip=document.getElementById('tip');
document.addEventListener('mousemove',e=>{const t=e.target.closest&&e.target.closest('[data-tip]');if(t){tip.style.display='block';tip.textContent=t.dataset.tip;tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px'}else tip.style.display='none'});
renderButtons();renderPages();
</script></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.run_dir))


if __name__ == "__main__":
    main()
