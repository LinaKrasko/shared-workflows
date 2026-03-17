import os
import subprocess
import json
import requests
import anthropic

# ── 1. Get the diff ──────────────────────────────────────────────
diff = subprocess.check_output(
    ["git", "diff", "origin/main...HEAD"],
    text=True
)

if not diff.strip():
    print("No diff found, skipping review.")
    exit(0)

print(f"Diff size: {len(diff)} characters")

# Truncate if too large (Claude has a context limit)
MAX_DIFF_CHARS = 15000
if len(diff) > MAX_DIFF_CHARS:
    diff = diff[:MAX_DIFF_CHARS] + "\n\n[diff truncated]"

# ── 2. Call Claude ───────────────────────────────────────────────
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

message = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=4000,
    system="""You are a senior code reviewer. Analyze the git diff provided and return a JSON array of review comments.

Each comment must have exactly these fields:
- "path": the file path (string)
- "line": the line number in the file (integer)
- "severity": one of "error", "warning", "suggestion", "praise"
- "body": your review comment

Rules:
- Comment on real issues — bugs, security problems, performance, bad practices
- Also add "praise" comments for good catches, meaningful bug fixes, and well-done refactoring
- Do not comment on formatting or style unless it causes a real problem
- Be specific and constructive, not generic
- Return ONLY the raw JSON array, no markdown, no explanation, no backticks""",
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

# ── 4. Post comments to GitHub ───────────────────────────────────
token   = os.environ["GITHUB_TOKEN"]
repo    = os.environ["GITHUB_REPOSITORY"]
pr_num  = os.environ["PR_NUMBER"]

headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json"
}

# Get the latest commit SHA on this PR
pr_resp = requests.get(
    f"https://api.github.com/repos/{repo}/pulls/{pr_num}",
    headers=headers,
    timeout=30
)
pr_resp.raise_for_status()
commit_sha = pr_resp.json()["head"]["sha"]

# Build inline comments (only those with valid path + line)
inline = [
    {
        "path": c["path"],
        "line": c["line"],
        "body": f"**[{c.get('severity', 'note').upper()}]** {c['body']}"
    }
    for c in comments
    if isinstance(c.get("line"), int) and c.get("path")
]

# Summary line for the review body
summary = f"Claude reviewed this PR and left {len(inline)} inline comment(s)." if inline else "Claude reviewed this PR and found no issues. Great job, the code looks clean!"

# Post the review
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