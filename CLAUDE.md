# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-script GitHub Actions utility (`code_review.py`) that automates PR code reviews using Claude AI. It is designed to be called from a GitHub Actions workflow.

## Running the script

```bash
ANTHROPIC_API_KEY=... GITHUB_TOKEN=... GITHUB_REPOSITORY=owner/repo PR_NUMBER=123 python code_review.py
```

All four environment variables are required. The script must be run from inside a git repository that has `origin/main` as a valid ref.

## Architecture

The script runs a 4-stage pipeline:

1. **Diff extraction** — `git diff origin/main...HEAD`, truncated to 15,000 chars
2. **Claude analysis** — sends diff to `claude-sonnet-4-6`, requests a JSON array of `{path, line, severity, body}` objects
3. **Response parsing** — parses the JSON; exits cleanly on empty or malformed responses
4. **GitHub posting** — fetches the PR's head commit SHA, then posts a single pull request review with inline comments via the GitHub REST API

## Key design constraints

- Claude is prompted to return **only raw JSON** (no markdown fences) so `json.loads()` works directly on the response text
- Comments missing a valid integer `line` or `path` are silently dropped before posting
- The GitHub review is posted as `"event": "COMMENT"` (not APPROVE/REQUEST_CHANGES), so it never blocks merging
