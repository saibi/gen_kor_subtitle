You are cross-reviewing a feature implemented by another agent. Do NOT modify any files.

Scope: plan [{ID}]
- Plan: {PLAN} (goals, flow, decisions {ID}-Q*, milestones {ID}-M*)
- Implementation note: {IMPL} (architecture, deviations from plan and why)
- Code: the files changed in commit {COMMIT} (see `git show --stat {COMMIT}`) and any code they reuse.
- Project rules: AGENTS.md

Review goals:
1. Does the implementation match the plan and the decisions? List planned items that are missing, partial, or deviate. For deviations explained in the implementation note, judge whether the reason is sound.
2. Correctness bugs, with concrete failure scenarios.
3. Safety: untrusted input (downloads, file names, external text), subprocess and external CLI usage.
4. Robustness issues a real user would hit (large inputs, encodings, network failures, interruption and resume).
5. Whether the self-checks (test_*.py) actually cover each milestone's completion criteria.

You may read code and run read-only commands (e.g. the self-check tests, grep). Avoid network-heavy calls.

Output — write in Korean, Markdown. This final message is saved as the review note:
# [{ID}] 교차리뷰 ({REVIEWER})
## 요약
<overall verdict, and a table of plan items vs implementation status>
## 지적사항
For each finding use an ID {ID}-R1, {ID}-R2, ... with:
- **분류**: `확실한 문제` (clear defect with an obvious fix) or `결정 필요` (needs a product/design decision)
- **심각도**: 높음/중간/낮음
- **위치**: file:line
- **내용**: what is wrong, with a concrete failure scenario
- **제안**: for 확실한 문제, the fix; for 결정 필요, 2-3 options with a recommended one marked (추천)
Order by severity. Only report real issues you verified by reading the code; no style nitpicks.
End with what you could not verify.
