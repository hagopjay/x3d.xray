# x3d.xray

Exploratory data analysis whose output is an X3D world you can fly through, with Claude plugged in.

x3d-ray reads the git history of a family of repositories and builds a page around one X3D 4.0 scene:

- a **time helix**: one turn per active day, angle is the hour, radius is the repo, colour is who committed, size is lines changed;
- a **tie constellation**: repos as nodes, with ties found by rule (commit hashes of one repo written in another, names in manifests,
  imports and CI, identical files, one Claude session in two repos, forks, the owner's stated lineage) and ties graded by hand;
- a **code city**: one district per repo at its tip, one building per file, a hot roof on heavy rewrites;
- **concept lattices** (formal concept analysis): files by the kinds of author that touched them, and repos by the kinds of author
  they have seen, with rules for the areas that change together and the commits that break each rule;
- **bridges** (betweenness): the Claude sessions that committed to more than one repo, the repos and sessions every path between
  two repos crosses in that session graph, and per repo the file on the most shortest paths between its other files (two files
  are linked when one commit changed both);
- a **pipeline row**: the stages of x3d.xray itself, the tool that drew all of it.

Around the scene the page carries dense readouts (signals, KPIs, per-repo table, charts), a tour, and an **Ask Claude** panel.

## AI-native, and aware of itself

- **One tool set, two consumers.** `x3dray/web/tools.js` is both the tool list the page hands to Claude (through the claude.ai
  Artifact `sample` capability) and the tool list of an MCP server (`x3dray/web/mcp.mjs`), so any agent can drive the same data.
  Data tools: `query_commits`, `activity`, `top_files`, `ties`, `traverse`, `concepts`, `bridges`, `signals`, `how_built` (and
  `overview` over MCP).
  Scene tools, page only: `show` (camera), `highlight` (light commits, x-ray the rest), `note` (pin a label in the scene), `color_by`.
- **Prose by Claude, numbers by code.** A spec (`specs/*.json`) is written by Claude with every number as a `{slot}`;
  `x3dray/spec.py` fills the slots from the profile, and the fact gate fails any digit outside a slot. Each view also makes one
  claim, marked with who is speaking: *git says* (counted by code), *the tool says* (its own description) or *Claude reads* (an
  interpretation). `python3 -m x3dray spec --brief` prints everything an AI author needs.
- **It describes itself.** `x3dray/selfdesc.py` records the pipeline (which stages involve a model and which do not), every
  module's sha256 and definitions, the source lines behind each signal, and what makes two things the same thing. `how_built`
  serves all of it to Claude and to MCP clients.
- **It is in its own data.** x3d.xray is one of the repos it x-rays: its commits are in the helix, its node is in the
  constellation and its files are in the city.
- **Claude knows what it is.** Claude wrote many of the commits it reads about, and the page tells it so, along with the fact that
  it is not those sessions and remembers none of them. Claude's share is always given with its denominator (all commits, without
  merges, without bots, by lines changed).

## Rules the data follows

- **One vocabulary.** Every repo name goes through the family's vocabulary: `x3d.ai` is `x3d_ai`, `.prov` is the provenance stage
  inside `x3d.ML` (there is no `.prov` repo). An unknown name is refused with the nearest names; nothing is guessed.
- **Unknown is not zero.** A coverage ledger records, per repo, how much history and tree was measured. What was not measured is
  reported as unknown and drawn as fog in the code city, never as an empty lot.
- **No witness, no tie.** Every tie carries its evidence (paths, lines, hashes, the owner's words); the witness gate fails a build
  that shows a tie without one.
- **Lookup or traverse.** Exact questions go to tables; multi-hop questions walk the tie graph. Claude says which route it used.
- **Set aside, then counted.** The file lattice, the area rules and the file co-change graph leave out merges (git lists no paths
  for them), private commits and mass edits (more than 15 files: a regeneration or a vendored drop would tie every file it touched to
  every other). The repo lattice and the author graph leave out only merges, and the session graph reads every commit with a trailer.
  Every answer says what its part read and how many it left out, and a list cut for size says how many it dropped.
- **No recorded session link, not no link.** Only commits with a Claude-Session trailer can link two repos through a session. Two
  repos one session committed to are linked directly, two joined only through other repos are linked through others, and two that no
  chain of sessions joins have no recorded session link; the ties may still join them. Betweenness is compared only within one
  cluster of files.
- **Viewpoints are claims.** Each Viewpoint is named by the claim it backs, not by where the camera stands.

## Privacy

- Emails never leave the harvester. Claude-Session URLs become short hashes. People in the family config are matched by sha256
  prefixes of their names and addresses, never by the text itself.
- Private repos show only commit times, identity classes and sizes: no subjects, hashes or file paths. In the concepts and bridges
  they enter as counts only.
- `data/` and `out/` are not committed. A build of a family that includes private repos stays on your machine or in a private
  Artifact; the privacy gate scans every file the build writes.

## Run it

Python 3.10+, git and Node 18+. The XSD gate needs `xmllint`; the live gate needs Playwright with Chromium; the Loom gate needs an
[x3d.onto](https://github.com/hagopjay/x3d.onto) clone, and the fork validator and XSD come from an
[x3d_mcp](https://github.com/hagopjay/x3d_mcp) clone. The page vendors X_ITE 16.7.0 (`x_ite` dist folder) and a TTF label font.

```sh
python3 -m x3dray harvest --family families/hagopjay-x3d.json --root ~/src --data data/hagopjay-x3d
python3 -m x3dray curated --data data/hagopjay-x3d --family families/hagopjay-x3d.json --masterclass path/to/masterclass   # optional
python3 -m x3dray structure --data data/hagopjay-x3d     # concepts.json and bridges.json
python3 -m x3dray profile --data data/hagopjay-x3d
python3 -m x3dray self    --data data/hagopjay-x3d --family families/hagopjay-x3d.json
python3 -m x3dray spec    --data data/hagopjay-x3d --spec specs/hagopjay-x3d.json
python3 -m x3dray build   --data data/hagopjay-x3d --out out/hagopjay-x3d
python3 -m x3dray page    --data data/hagopjay-x3d --out out/hagopjay-x3d --xite path/to/x_ite/dist --font path/to/UbuntuMono-R.ttf
python3 -m x3dray gate    --data data/hagopjay-x3d --out out/hagopjay-x3d
python3 -m x3dray receipt --data data/hagopjay-x3d --out out/hagopjay-x3d
```

`python3 -m x3dray all ...` runs the whole chain. `out/<family>/publish.json` maps every file of the page for publishing as an
Artifact (declare the `sample` capability so Ask works).

## Use it from any agent

```sh
claude mcp add x3d-xray -- node /path/to/x3d.xray/x3dray/web/mcp.mjs --bundle /path/to/out/hagopjay-x3d/bundle.json
python3 -m x3dray tools     # every tool with its schema: shared, MCP only, page only
```

## Gates and the receipt

| gate | checks |
| --- | --- |
| xsd | the scene validates against the X3D 4.0 schema |
| loom | x3d.onto's Loom gate on the scene, with the x3d_mcp fork's validator |
| fact | no digit outside a slot, every slot resolves, every claim is backed |
| privacy | no address, session URL or secret-shaped string in any file the build wrote, and no private repo itemised (no sha, subject, file, path or area) in the commits, concepts or bridges, or in the gate results themselves |
| witness | no tie without evidence, and the scene draws no tie the data lacks; the concepts and bridges are recomputed from the dataset and must match, they pass the structure stage's own check again, and the scene draws no concept, session or bridge file they lack. That check re-derives from the commits, apart from the lattice and graph code: every cited hash is a commit that did what it is cited for (touched that file or both files, is that author's or that kind of author's, in that repo, carries that session, is the first of its kind there, is that mass edit); every count and each part's set-asides; every closed set of kinds and the lines between them; every area rule that meets the thresholds; the file graphs' sizes, clusters, degrees, cut vertices and which files lie on a shortest path between two others; and the session and author graphs' passage points, cut vertices and betweenness, by brute force. The size of each file's betweenness is checked against a brute-force oracle in the tests |
| tools | every tool runs within the 32 KB result limit, aliases resolve, unknown names are refused, the MCP server answers |
| live | Chromium under a strict content policy: the scene loads and draws every label, the scene tools work, a click opens a card, and Ask runs end to end against a stand-in for the sample runtime |

The receipt is one sha256 over every input, module and output, plus the gate verdicts. The page shows it; `how_built` returns it.

## Layout

```
x3dray/harvest.py    git history and tip trees; identities by hash; privacy at the source
x3dray/ties.py       ties between repos, found by rule and graded C, L or S with evidence
x3dray/curated.py    hand-graded ties imported from a masterclass, addresses scrubbed
x3dray/structure.py  concepts and bridges, computed with no model, and the check that re-derives them from the commits
x3dray/fca.py        formal concept analysis: concepts, covers, levels, rules with their exceptions
x3dray/centrality.py betweenness (Brandes, exact fractions), cut vertices, passage points
x3dray/profile.py    the signals and the coverage ledger, computed with no model
x3dray/selfdesc.py   the tool's description of itself
x3dray/spec.py       slot filling and the fact gate
x3dray/compiler.py   the X3D scene and its layout
x3dray/page.py       the Artifact folder: bundle, page, vendored X_ITE, font
x3dray/gates.py      the gates;  x3dray/receipt.py  the receipt
x3dray/web/          app.js (the page), tools.js (the shared tools), mcp.mjs (the MCP server),
                     selftest.mjs (the tools gate), live_gate.cjs (the live gate), page.css
families/            one config per repo family: repos, identities (hashed), vocabulary, lineage
specs/               one spec per family, written by Claude, numbers as slots
tests/               python3 -m unittest discover -s tests
```

## Limits

- Lines changed come from git numstat, so vendored and generated files count in full.
- Renames are not followed, so a renamed file is two files in the concepts and bridges. Squashed or rebased history hides who did
  the work.
- The live gate copies the Artifact host's content policy as far as it can be seen from outside, so a pass there is likely, not
  certain, on claude.ai. Ask is exercised against a stand-in for the sample runtime; only claude.ai runs the real one.
