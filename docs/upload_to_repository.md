# Repository upload procedure

This pre-human package must remain private while the planned human evaluation is
being recruited or collected. A public repository can expose card material or
method details that could compromise the evaluation boundary.

## Private repository now

Create a private repository under the researcher's account, then run:

```powershell
.\PUSH_TO_GITHUB.ps1 -RepositoryUrl "https://github.com/<account>/<repository>.git"
```

The script asks only for the repository URL as a PowerShell parameter. Do not put
a personal access token, API key, or password inside this repository.

## Public release later

After human collection is closed, data are cleaned, the public release builder
passes its gates, and a final repository URL is selected:

1. Add the final URL to `CITATION.cff`.
2. Rebuild the final public release from the private working archive.
3. Run all public checks from a clean clone.
4. Create an immutable tag and DOI snapshot.
5. Update the manuscript's data-availability statement.

Never publish `Private/`, raw human responses, consent records, participant contacts,
API keys, or local account/session metadata.
