# x3d.xray

## Development pass: evidence boundaries and bridge laboratory

This working development pass adds a deterministic, synthetic investigation lab:

```sh
python3 -m x3dray lab --out out/bridge-lab
python3 -m http.server 8765 --bind 127.0.0.1 --directory out/bridge-lab
# Open http://localhost:8765
```

The map, exact graph scores, witnesses, four-lens comparison, node-removal experiments
and investigation save/replay work without network access. `--xite path/to/x_ite/dist`
vendors renderer assets for offline 3D. Without local assets, selecting 3D downloads
pinned X_ITE 16.7.0. Every observation in the lab is synthetic; it is not a family harvest.
The small fixture's hub policy is explicitly 65%, separate from the production analysis policy.

The existing pipeline now has stronger boundaries:

- Git numstat and tree paths share NUL-safe decoding; linked worktrees and configured
  fork identities are recognized. Empty/small profile populations report unavailable
  signals rather than inventing dates or percentages. Empty scenes are explicit.
- Tie grading reads all detector outcomes independently of displayed evidence. Full
  object IDs stay internal for pin validation; ambiguous abbreviations are refused.
- Spec structure is validated, and the fact gate re-renders stored source templates
  against current profile/self data. This checks reference/format consistency, not
  free-prose entailment or independent authentication of the stored source.
- Page builders register their emitted assets. The publication map, privacy scan and
  receipt consume that set; unrelated files in the output folder do not become public.
- Gates expose `ok` for development and `release_ready` for all mandatory gates passing.
  Use `gate --release` or `all --release` to require release readiness. A skipped gate
  is incomplete for release. Scene presence is checked; independent geometry-to-data
  semantic reconstruction remains future work.
- `all` uses fresh data/output staging directories, retains failed diagnostics, and
  preserves prior destinations as named backups on successful promotion. Data and
  output destinations must be distinct and non-nested. Promotion is sequential with
  rollback, not an atomic two-directory or concurrency-safe transaction. Do not serve
  or mutate the destinations during a build.
- Passage-point production analysis uses an iterative block-cut index. The verifier
  retains independent removal-based reasoning with cached partitions. Small graph
  oracles check the optimized implementation; exact weighted betweenness is unchanged.
- Shared tools enforce a final UTF-8 result bound and preserve whole returned evidence
  records. Emergency truncation is explicit; full pagination remains future work.
  MCP validates message shapes, survives malformed requests, bounds lines and explicitly
  supports the 2025-06-18 profile; complete protocol conformance is not claimed.

`experiments/passage-index-benchmark.json` records three-repetition synthetic timings
and exact output hashes. Its 121-node analysis-plus-verifier median fell from 10.14 s
to 0.63 s on the audit host. This is a graph benchmark, not a full-pipeline or GPU result.

Run `python3 -m unittest discover -s tests -v`. Optional DOM integration tests:

```sh
npm install --prefix /tmp/lab-dom-tests --no-audit --no-fund jsdom@31.0.0
NODE_PATH=/tmp/lab-dom-tests/node_modules node experiments/test_lab_dom.cjs out/bridge-lab/index.html
```

Node 24 is used in CI for the optional DOM test dependency. Python runtime code has no
third-party dependency. Broad style enforcement, non-UTF-8 path export policy, batch
blob reads, fully generic exhibition themes and live hardware graphics verification
remain separate work. New CI configuration is provided; remote CI has not been run here.

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
  Data tools: `query_commits`, `activity`, `top_files`, `ties`, `traverse`, `architecture`, `concepts`, `bridges`, `eda`, `signals`,
  `how_built` (and `overview` over MCP). `eda` has nine topics: `ledger`, `finding`, `audit`, `profile`, `flags`, `rhythm`,
  `concentration`, `slice` and `method`. A grade is a robustness check over declared variations, never a probability; slices are
  descriptive and ungraded.
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
  every other). The repo lattice and the author graph leave out only merges, and the session graph reads every commit with a
  Claude-Session trailer. Every answer says what its part read and how many it left out, and a list cut for size says how many it
  dropped.
- **No recorded session link, not no link.** Only commits with a Claude-Session trailer can link two repos through a session. Two
  repos one session committed to are linked directly, two joined only through other repos are linked through others, and two that no
  chain of sessions joins have no recorded session link; the ties may still join them. Betweenness is compared only within one
  cluster of files.
- **Viewpoints are claims.** Each Viewpoint is named by the claim it backs, not by where the camera stands.

## Exploring under rules

`python3 -m x3dray eda` runs after `structure` and writes `data/eda.json`. It is quasi-exploratory: what may be looked at is
declared before the data is read, and what is found is graded by how well it survives declared checks.

- **A fixed family.** `looks.DECLARED` lists 12 looks: six contrasts between the two largest of you, Claude and bots (lines changed,
  files changed, seconds since the previous commit, author hour in four-hour bins, weekday, repo mix) and six drift looks (the later
  half against the earlier half of one kind of author's commits, for lines, hour and weekday, once for each of the pair). The family
  size is fixed at m = 12 whatever the data; a look that cannot run enters the false-discovery correction with p = 1 and says why it
  did not run. The manifest hash of the declared family is stored and checked.
- **Checks, not tests of belief.** Each look gets an effect size (Cliff's delta, or total variation for hour, weekday and repo), an
  add-one label-permutation count with a seeded SplitMix64 stream, a Benjamini-Hochberg q over the 12, and these robustness
  checks: four forks (all commits, without merges, default branch only, without mass edits of more than 15 files; forks that hold exactly
  the same commits count once, and `coverage.distinct_forks` and `fork_same_as` say how many differ), leave-one-repo-out,
  per-repo strata that must not show an opposite effect, and interleaved-week halves that must each replicate the direction.
- **Grades.** `looks.grade` is a pure function applied in a fixed order: not run or effect below the floor or add-one count above
  1/20 gives no grade; q above 1/10 or an opposite effect in a repo gives Suspected; a failed or unavailable check (including no
  repo holding 20 commits of each group, so the strata could not be run, or fewer than three distinct forks) gives Likely, and so does
  a q above 1/20 (Confirmed needs q of at most 1/20, stored as `ledger.q_confirm`); all checks passing at q of at most 1/20 gives Confirmed.
  The reason is stored with every grade. `say_at_most` gives the strongest wording each grade
  allows. A grade is a robustness check over declared variations, never a probability; `contrast:repo` never exceeds Likely.
- **Slices are ungraded.** The `eda` tool's `slice` topic computes exact summaries of the commits matching repo, who, dates, session, hour, weekday, size (`min_lines`, `max_lines`)
  and flag (`rule`, `grade`) filters (quantiles, MAD, power-of-two bins, Gini) in the browser or over MCP, and counts how many looks have been asked this session next to the 12
  declared (distinct slices, plus `query_commits`, `activity` and `highlight` calls that carry an hour, weekday, size or flag filter; the
  `ledger` topic shows the count too). A `from`, `to` or `day` that is not a real YYYY-MM-DD date is refused, not read as an empty slice. It never grades. Each `eda` topic refuses an argument it does not read (for example `from` on `profile`), so an answer
  is never for a different question than the one asked.
- **Recompute in your browser.** `eda` topic `recompute` runs the 12 declared looks again from the bundle's commit rows with a JavaScript twin
  of `stats.py` and `looks.py` (a synchronous SHA-256, SplitMix64 on two 32-bit halves, exact integer Cliff and total-variation counts, the
  add-one permutation null, Benjamini-Hochberg q and the grade rule) and compares the canonical text with the stored section byte for byte.
  It answers `match: true` with both digests, or names the differing paths (never their text). It checks known answers first (SHA-256 of
  "abc", the published SplitMix64 words, the manifest hash) and refuses the call before drawing anything if the permutations would exceed
  25 million draws or the rows break the `stats.py` row contract. Python stays the specification; `tests/test_eda_infer.py` proves the two agree on
  ties, huge values, no author pair and a 24,000-commit bundle.
- **Ask whether slice A differs from slice B (`compare`).** `eda` topic `compare` takes two disjoint commit filters (`a`, `b`, the `slice` vocabulary), a
  `measure` (lines, files, gap_s, hour4, weekday, repo) and a `fork`, and returns the effect, an exact add-one label-permutation count, and a
  Benjamini-Hochberg q over every look asked in this tool set plus the 12 declared, with that denominator printed in the answer. The seed comes from the
  two groups' value lists in digest order, so asking B against A, or spelling a filter another way, is the same question with the same stream: a
  question cannot be re-rolled. It is always **ungraded** (`grade: null`): the robustness grades need the declared checks, which only the 12
  declared looks have. It refuses a group under 20 commits or a bill above 4 million draws instead of thinning the permutations (at most 30
  distinct comparisons per tool set). The count of looks restarts from 12 on a page reload, and the answer says so (`counted_in`). `looks.compare`
  is the Python reference.
- **Flags.** Two rules mark commits for a second look, graded by how many distinct forks agree: `size_high` (unusually many lines for the
  commit's kind of author) and `gap_long` (after an unusually long quiet spell in its repo). Light them with
  `highlight {flag, flag_grade, min_lines, max_lines, hour, weekday}`; the same filters work in `query_commits` and `activity`. The filter reads the stored flag lists, which stop at 200 per
  rule and grade; when a rule and grade the filter would reach has more flags than that, it refuses and names the grades that are
  listed in full, rather than counting only the commits listed.
- **Audit and profile.** Eight data checks (timestamp range, duplicate object IDs, duplicate fingerprints, same-second runs, empty
  commits, stat-free changes, merge contract, class residual), per-slice profiles over four forks, quiet hours and bursts, and per-repo
  churn concentration. The timestamp check needs a parsable `harvested_at` to test for commits after the harvest; without one it
  reports `unavailable` rather than a pass. A dataset the stage cannot count (a time beyond plus or minus 8.64e12 seconds, a
  negative or non-integer line count or file churn, a repo without a name) stops with one `eda: ...` line and writes nothing.
- **Independent check.** `eda.verify` and `looks.verify` recompute everything by naive routes (pair loops, Fraction histograms,
  textbook BH); the witness gate runs them again. No float appears in `eda.json`, and the file is byte-identical across runs.

Limits: times are UTC author times, not local. Commits are treated as exchangeable under the permutation null, which they are not
(they cluster in sessions). Benjamini-Hochberg assumes independent or positively dependent looks; these share commits, so q is
indicative. Private repos contribute counts and times only, and concentration of an unmeasured tree is reported as unknown. Flag
lists keep at most 200 commits per rule and grade and say how many were omitted. Renames are not followed. The Gallery scene view of
this data is a follow-up; the scene gate's scope is unchanged.

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
python3 -m x3dray eda     --data data/hagopjay-x3d     # eda.json: data audit, size and time profiles, graded looks, flags
python3 -m x3dray profile --data data/hagopjay-x3d
python3 -m x3dray self    --data data/hagopjay-x3d --family families/hagopjay-x3d.json
python3 -m x3dray spec    --data data/hagopjay-x3d --spec specs/hagopjay-x3d.json
python3 -m x3dray build   --data data/hagopjay-x3d --out out/hagopjay-x3d
python3 -m x3dray page    --data data/hagopjay-x3d --out out/hagopjay-x3d --xite path/to/x_ite/dist --font path/to/UbuntuMono-R.ttf
python3 -m x3dray gate    --data data/hagopjay-x3d --out out/hagopjay-x3d
python3 -m x3dray receipt --data data/hagopjay-x3d --out out/hagopjay-x3d
```

`python3 -m x3dray all ...` runs the whole chain (harvest, curated, structure, eda, profile, self, spec, build, page, gate, receipt). `out/<family>/publish.json` maps every file of the page for publishing as an
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
| privacy | no address, session URL or secret-shaped string in any file the build wrote, and no private repo itemised (no sha, subject, file, path or area) in the commits, concepts or bridges, or in the gate results themselves. It also scans `bundle.eda` and checks that every audit witness is a public commit hash and every flag index points at a commit of the bundle |
| witness | no tie without evidence, and the scene draws no tie the data lacks; the concepts and bridges are recomputed from the dataset and must match, they pass the structure stage's own check again, and the scene draws no concept, session or bridge file they lack. It also recomputes `eda`: the bundle's section, `data/eda.json` and a fresh run of the stage on the dataset must agree, the section must pass the stage's independent check, and the bundle's commits must be the rows the stage indexed its flags by. That check re-derives from the commits, apart from the lattice and graph code: every cited hash is a commit that did what it is cited for (touched that file or both files, is that author's or that kind of author's, in that repo, carries that session, is the first of its kind there, is that mass edit); every count and each part's set-asides; every closed set of kinds and the lines between them; every area rule that meets the thresholds; the file graphs' sizes, clusters, degrees, cut vertices and which files lie on a shortest path between two others; and the session and author graphs' passage points, cut vertices and betweenness, by brute force. The size of each file's betweenness is checked against a brute-force oracle in the tests |
| scene | independent XML reader compares the declared constellation Sphere/Box world geometry, used IndexedLineSet coordinates, endpoint attachment, identity, source witnesses and grade colours against the plan; missing legacy plans skip and block release readiness. Other regions, visibility, animation and rendered pixels are outside this contract |
| tools | every tool runs within the 32 KB result limit, aliases resolve, unknown names are refused, the MCP server answers |
| live | Chromium under a strict content policy: the scene loads and draws every label, the scene tools work, a click opens a card, and Ask runs end to end against a stand-in for the sample runtime |

The receipt is one sha256 over every input, module and output, plus the gate verdicts. The page shows it; `how_built` returns it.

## Architecture Observatory

Build a tinkerable synthetic specimen from four temporary real Git repositories:

```sh
python3 -m x3dray observatory --out out/observatory
python3 out/observatory/start_lab.py
```

The printed localhost URL opens the geometry overlay, typed path explorer and replayable investigation. The export includes 24 synthetic commits, four glyphs, six semantic polylines, seven typed relation records and 13 actual XML audit cases. The independent Python verifier runs during export; the browser displays its recorded results. It uses the production shared `architecture` query tool for live filtering. Browser event hashing needs a secure context, such as localhost. No real family or private history is bundled.

For offline 3D, supply `--xite path/to/x_ite/dist --font path/to/font.ttf --font-license path/to/font-license.txt`. Without vendored assets, optional 3D attempts the pinned X_ITE CDN; the analytic overlay has no external dependencies. The supplied font's redistribution notice can be included in the same manifest. Static checks do not certify runtime pixels.

Download a selected XML case, plan and witness bundle from the lab, then re-run:

```sh
python3 -m x3dray verify-scene --scene selected.x3d --plan scene-plan.json --bundle witness-bundle.json
python3 experiments/scene_mutation_audit.py --output-json scene-mutations.json
```

Ten deliberate violations are rejected; inverse nested transforms preserve world geometry and pass. A line added outside the semantic layers is a visible scope probe: acceptance of the instrumented subset does not certify that extra geometry. DEF/USE expansion is limited by a shared 250,000-operation budget in addition to XML size, element and depth limits.

`layout.json.scene_plan` separates identities and expected world geometry from the emitted XML. The checker does not import the compiler or reuse its transform code. This establishes correspondence to the supplied plan and observations; it does not authenticate those inputs. Receipts now include nested package templates and scripts.

`bundle.json.relations` separates predicates before traversal:

| source observation | projected predicate | traversal meaning |
| --- | --- | --- |
| configured use | `declares_use` | directed declaration |
| static dependency reference | `dependency_reference` | directed reference; execution is unverified |
| recorded revision pin | `pins_revision` | directed pin |
| matching blobs / shared session | `shares_blob` / `shared_session` | symmetric association |
| stated lineage | `claims_lineage_from` | successor to predecessor; assertion, not proven Git ancestry |
| curated prose | `curated_association` | symmetric association; prose direction remains annotation |

Use `architecture` with `from`, optional `to`, `predicates`, `direction`, `grades`, `max_hops` and `limit`. Directions are `outgoing`, `incoming` or explicitly undirected `association`. It counts shortest node paths exactly within the filter and hop bound; parallel records support a hop rather than multiply node paths. Every shortened response reports omissions. C/L/S are evidence grades, not probabilities. The legacy `traverse` tool remains an association query.

Investigation logs bind query endpoints, filters, selections and requested mutations to source, analysis and implementation identities. Python and browser reducers validate the same bounded hash-chain contract. A chain is not actor authentication and does not prove an experiment ran; verifier output is separately recorded. Replaying a history from a different analysis is refused.

DOM integration (requires `jsdom@31.0.0`) is exercised by `experiments/test_observatory_dom.cjs`; it includes a renderer stand-in for scene reuse. Actual WebGL, layout and full-family release gates still require their own environment and evidence.

## Immutable witnesses, time and measured scale

The Continuum Observatory extends the same lab with exact public Git file locators, independent observation/validity lenses and recorded scale experiments. Python 3.10+, Git with `--no-lazy-fetch` support, and Node are required for the complete workflow. Missing Git object/capability checks return `unavailable`; they never trigger a promisor fetch. Optional jsdom 31.0.0 adds explicitly labelled SVG DOM construction measurements.

```sh
python3 -m x3dray fixture-sources --out synthetic-sources
python3 -m x3dray verify-witness --repo synthetic-sources/evidence --repo-id github:synthetic/evidence --locator synthetic-sources/witnesses/0000.json
python3 -m x3dray scale --out scale-evidence --jsdom-modules path/to/node_modules
python3 -m x3dray observatory --out out/continuum --benchmark scale-evidence/scale-benchmark.json
python3 out/continuum/start_lab.py
```

Use an empty source output directory. The four fixture repositories are synthetic and local. Locators name a canonical caller-bound repository ID, full commit and blob IDs with object format, raw content SHA-256, exact byte/line span and span SHA-256, and detector/version. The local verifier rehashes the commit/tree/blob chain with replacement and lazy fetching disabled. Moving HEAD or modifying the worktree does not change the addressed evidence. Caller identity, remote ownership and the detector's interpretation remain separate trust questions. Non-file assertions state that no immutable file locator is available. Private-touching relations export aggregates, with no private content hashes or locators.

The `architecture` query accepts paired `at` (timezone-qualified ISO timestamp) and `time_mode`, plus `unknown_time` (`exclude` default, or `include`):

| time mode | meaning |
| --- | --- |
| `observed_by` | supplied observation time ≤ query instant; makes no lifetime claim |
| `valid_at` | explicit claimed start ≤ instant < end; every hop uses the same instant |

Unknown-time paths are labelled provisional when included. Public explicit `observed_at` and `{start,end}` `valid_time` fields survive curated import. Commit author dates and legacy `first_date` are never converted into lifetimes. A validity query can use later-recorded assertions about the past; combining validity with a historical knowledge cutoff is a separate future query mode. `set_time` investigation events save and replay these choices with strict UTC/microsecond normalization.

The benchmark generates 10/100/1,000 entities for chain, modular and branching graphs, with long Unicode IDs, parallel typed observations and isolated nodes. These are generated source records, not thousands of harvested repositories. It runs the production constellation compiler, independent verifier, typed projection, Python queries and shared JavaScript tool against an independent path oracle. Seven measured repetitions follow two warmups by default; raw samples, nearest-rank p95, source fingerprints, environment, budget refusals and clearly scoped memory probes are retained. The packaged JavaScript harness works from an installed wheel.

The fixed-radius layout produced 7,936 overlapping glyph pairs at 1,000 entities. Adaptive concentric rings reduce that fixture's glyph overlaps to zero and resize its region/viewpoint. This establishes world-space glyph separation; labels, edge crossings, screen readability and GPU performance need separate checks. All 1,000 glyphs are verified even though the independent observed-detail export is capped at 200 records. The lab's full plan preview discloses this distinction.

Modern scene plans also bind a formal source digest: first four substantive public witnesses, display strings capped at 160 characters, complete normalized locators and aggregate counts. A complete `scene_sources` ledger explains omitted inputs. The independent checker detects altered source samples and coordinated removals from XML/plan; private hashes depend on aggregate counts only. A retained trusted receipt binds the entire exported bytes beyond this bounded sample. Neither mechanism authenticates the publisher. Legacy geometry plans remain inspectable with verify-scene, but cannot satisfy the current release gate until regenerated with source binding.

## Layout

```
x3dray/harvest.py    git history and tip trees; identities by hash; privacy at the source
x3dray/ties.py       ties between repos, found by rule and graded C, L or S with evidence
x3dray/curated.py    hand-graded ties imported from a masterclass, addresses scrubbed
x3dray/structure.py  concepts and bridges, computed with no model, and the check that re-derives them from the commits
x3dray/stats.py      exact integer statistics: quantiles, MAD, Gini, Cliff counts, total variation, SplitMix64, permutation counts, Benjamini-Hochberg
x3dray/eda.py        the data audit, size and time profiles, rhythm, concentration, flags, and the independent check that re-derives them
x3dray/looks.py      the declared family of 12 looks, their robustness checks and grades, and the independent check that re-derives them
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
tests/               python3 -m unittest discover -s tests (test_stats_oracle, test_eda, test_looks,
                     test_eda_pipeline, test_eda_js_parity, test_eda_tool, with tests/eda_support.py)
```

## Limits

- Lines changed come from git numstat, so vendored and generated files count in full.
- Renames are not followed, so a renamed file is two files in the concepts and bridges. Squashed or rebased history hides who did
  the work.
- The live gate copies the Artifact host's content policy as far as it can be seen from outside, so a pass there is likely, not
  certain, on claude.ai. Ask is exercised against a stand-in for the sample runtime; only claude.ai runs the real one.
