import os
import subprocess
import json
import requests
import anthropic
import fnmatch

# ── 0. Skipped files ─────────────────────────────────────────────
SKIP_FILES = [
    ".env", ".env.*",
    "*.pem", "*.key", "*.cert",
    "secrets.json", "credentials.json",
    "package-lock.json", "yarn.lock", "poetry.lock", "Pipfile.lock",
    "*.min.js", "*.min.css",
    "__snapshots__",
    "**/migrations/*.py", "**/migrations/*.sql",
    "*.md",
]

def should_skip(filepath):
    parts = filepath.replace("\\", "/").split("/")
    for pattern in SKIP_FILES:
        if fnmatch.fnmatch(filepath, pattern):
            return True
        if fnmatch.fnmatch(os.path.basename(filepath), pattern):
            return True
        if pattern in parts:
            return True
    return False

# ── 1. Get the diff ──────────────────────────────────────────────
diff = subprocess.check_output(
    ["git", "diff", "origin/main...HEAD"],
    text=True
)

if not diff.strip():
    print("No diff found, skipping review.")
    exit(0)

# Filter skipped files
filtered_lines = []
skip = False
for line in diff.splitlines():
    if line.startswith("diff --git"):
        parts = line.split(" b/")
        filepath = parts[-1] if len(parts) > 1 else ""
        skip = should_skip(filepath)
    if not skip:
        filtered_lines.append(line)

diff = "\n".join(filtered_lines)

if not diff.strip():
    print("Nothing left to review after filtering.")
    exit(0)

print(f"Diff size: {len(diff)} characters")

# Truncate if too large
MAX_DIFF_CHARS = 100000
if len(diff) > MAX_DIFF_CHARS:
    diff = diff[:MAX_DIFF_CHARS] + "\n\n[diff truncated]"

# ── 2. Call Claude ───────────────────────────────────────────────
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

message = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=4000,
    system="""You are a senior software engineer doing a thorough, balanced code review.
A great review has two equal jobs: catch real problems AND recognize good work.
If you only leave negative comments you are not doing your job fully.

## Praise first — always look for these (severity: "praise")
Before looking for problems, scan the diff for good work.
Ask yourself: what did this developer do RIGHT?

Praise these when you see them — be specific, never generic:
- A bug that was caught and correctly fixed — name the bug and explain why the fix works
- Security handled properly (input validated, auth checked, query parameterized) — say what attack it prevents
- Edge case covered that most developers would have missed — name the edge case
- Async code handled correctly (proper await, error caught, no race condition)
- Good use of transactions or rollback logic — explain what data it protects
- A refactor that genuinely reduced complexity — say what got simpler and why that matters
- Meaningful performance improvement (caching added, query optimized, N+1 fixed) — quantify if possible
- Clean error handling that gives the caller useful information

Do NOT praise:
- Routine, expected code that any developer would write
- Formatting or naming that is simply acceptable
- Code that is fine but not noteworthy
Praise should feel earned. If you praise everything, nothing means anything.

## Errors — always flag these (severity: "error")
- Bugs and logic errors (off-by-one, wrong conditions, unreachable code)
- Security vulnerabilities (SQL injection, XSS, exposed secrets, missing auth)
- Data loss risks (missing transactions, unhandled failures, overwriting data)
- Crashes waiting to happen (unhandled exceptions, null/undefined access, infinite loops)
- Broken async code (missing await, unhandled promise rejections, blocking the event loop)

## Warnings — flag if clearly problematic (severity: "warning")
- Performance issues (N+1 queries, unnecessary loops, fetching more than needed)
- Memory leaks (unclosed connections, event listeners never removed)
- Race conditions (shared mutable state, concurrent writes without locks)
- Missing error handling on operations that can fail (network, DB, file I/O)
- Hardcoded values that should be environment variables

## Suggestions — light notes only (severity: "suggestion")
- Confusing names that genuinely hurt readability
- Functions doing too many things
- Missing comments on non-obvious logic
- Inconsistent patterns compared to the rest of the file

## What NOT to comment on
- Formatting, indentation, whitespace — that is what linters are for
- Personal style preferences with no functional impact
- Lock files, generated files, migration files, minified files

## How to write each comment type

Praise:
- "This null check on line X prevents a crash that would occur when [specific scenario]. Good catch."
- "Wrapping this in a transaction means if [step 2] fails, [step 1] rolls back. This protects data integrity."
- Never: "Nice work!" / "Good job!" / "Well done!" — these are empty

Errors:
- Always say WHAT will go wrong and WHEN: "This will throw a TypeError when user is null"
- Always say HOW to fix it: "Add a null check before accessing user.profile"
- Never say "consider" for errors — it is not optional

Warnings:
- Explain the risk: "This opens a new DB connection on every request — under load this will exhaust the connection pool"
- Give a concrete fix

Suggestions:
- One or two lines max
- Explain the benefit, not just the change

## Balance target
Aim for at least one praise comment per meaningful improvement in the diff.
If a developer fixed a bug, refactored something, or added proper error handling — say so.
A review with 5 problems and 0 praise is incomplete unless the diff has genuinely nothing good in it.

## Output format
Return ONLY a raw JSON array. No markdown, no explanation, no backticks.
Each item must have exactly:
- "path": file path string
- "line": integer line number
- "severity": "error" | "warning" | "suggestion" | "praise"
- "body": your comment

If you find nothing worth commenting on, return: []""",
    messages=[{
        "role": "user",
        "content": f"Please review this diff:\n\n{diff}"
    }]
)

# ── 3. Parse Claude's response ───────────────────────────────────
raw = message.content[0].text.strip()

if raw.startswith("```"):
    raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

try:
    comments = json.loads(raw)
except json.JSONDecodeError:
    print("Claude returned non-JSON response:")
    print(raw)
    exit(1)

print(f"Claude found {len(comments)} comment(s).")

# ── 4. Build summary ─────────────────────────────────────────────
counts = {"error": 0, "warning": 0, "suggestion": 0, "praise": 0}
for c in comments:
    sev = c.get("severity", "suggestion")
    if sev in counts:
        counts[sev] += 1

if counts["error"] > 0:
    verdict = "❌ Changes requested — errors must be fixed before merging."
elif counts["warning"] > 0:
    verdict = "⚠️ Warnings found — please review before merging."
elif counts["suggestion"] > 0 or counts["praise"] > 0:
    verdict = "✅ Looks good — a few notes below."
else:
    verdict = "✅ No issues found. Code looks clean!"

summary = f"""{verdict}

| Severity | Count |
|---|---|
| 🔴 Errors | {counts['error']} |
| 🟡 Warnings | {counts['warning']} |
| 🔵 Suggestions | {counts['suggestion']} |
| 🟢 Praise | {counts['praise']} |

*Reviewed by Claude — automated review, always apply your own judgement.*"""

# ── 5. Format inline comments ────────────────────────────────────
ICONS = {
    "error":      "🔴 ERROR",
    "warning":    "🟡 WARNING",
    "suggestion": "🔵 SUGGESTION",
    "praise":     "🟢 NICE CATCH",
}

inline = [
    {
        "path": c["path"],
        "line": c["line"],
        "body": f"**{ICONS.get(c.get('severity', 'suggestion'))}**\n\n{c['body']}"
    }
    for c in comments
    if isinstance(c.get("line"), int) and c.get("path")
]

# ── 6. Post to GitHub ────────────────────────────────────────────
token  = os.environ["GITHUB_TOKEN"]
repo   = os.environ["GITHUB_REPOSITORY"]
pr_num = os.environ["PR_NUMBER"]

headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json"
}

pr_resp = requests.get(
    f"https://api.github.com/repos/{repo}/pulls/{pr_num}",
    headers=headers,
    timeout=30
)
pr_resp.raise_for_status()
commit_sha = pr_resp.json()["head"]["sha"]

response = requests.post(
    f"https://api.github.com/repos/{repo}/pulls/{pr_num}/reviews",
    headers=headers,
    json={
        "commit_id": commit_sha,
        "body": summary,
        "event": "COMMENT",
        "comments": inline
    }
)

print(f"GitHub API response: {response.status_code}")
if response.status_code in (200, 201):
    print("Review posted successfully.")
else:
    print(f"GitHub API error {response.status_code}: {response.text}")
    exit(1)