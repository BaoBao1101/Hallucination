param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryUrl
)

# Run after creating an empty GitHub repository under your own account.
# Do not commit raw source archives, Private/ folders, API keys, or Human Evaluation raw responses.

Set-Location $PSScriptRoot

git init
git branch -M main
git add .
git status
git commit -m "Prepare pre-human CHVD reproducibility candidate and conservative manuscript draft"

git remote add origin $RepositoryUrl
git push -u origin main

# Optional immutable checkpoint after reviewing repository contents:
git tag -a v0.9.2-prehuman-submission-ready -m "Pre-human candidate: frozen LLM-evaluator evidence and conservative manuscript"
git push origin v0.9.2-prehuman-submission-ready
