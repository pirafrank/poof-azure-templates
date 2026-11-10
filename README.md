# poof Azure DevOps Templates

An [Azure DevOps YAML step template](https://learn.microsoft.com/en-us/azure/devops/pipelines/process/templates)
that installs [poof](https://github.com/pirafrank/poof) and optionally installs pre-built binaries
of your choice from GitHub Releases into your pipeline.

## What it does

1. Bootstraps `poof` (a binary installer for GitHub Releases) at the requested version.
2. Optionally installs any number of GitHub Release packages in one step.
3. Prepends `poof` and packages bin dirs to `PATH` via `##vso[task.prependpath]` so installed
   tools are available in subsequent steps in the same job.

## Setup

### 1. Create a service connection to GitHub

In your Azure DevOps project go to **Project Settings → Service connections → New service connection → GitHub**.
Note the connection name — you'll use it in the `resources` block below.

### 2. (Optional) Store a GitHub PAT as a secret variable

In your pipeline or variable group, add a secret variable (e.g. `GITHUB_TOKEN`) containing
your GitHub personal access token. Pass its name via `githubTokenVariable`.
If the variable is absent or empty, requests are unauthenticated. The repository service
connection does not supply this token to poof. `System.AccessToken` authenticates to
Azure DevOps and cannot be used as a GitHub token.

## Usage

### Minimal — install poof only

```yaml
pool:
  vmImage: ubuntu-latest

resources:
  repositories:
    - repository: poofTemplates
      type: github
      name: pirafrank/poof-azure-templates
      ref: refs/tags/v1.0.0
      endpoint: my-github-service-connection

steps:
  - template: poof.yml@poofTemplates

  - bash: poof --version
    displayName: "Verify poof"
```

### Full — install poof and tools

```yaml
pool:
  vmImage: ubuntu-latest

resources:
  repositories:
    - repository: poofTemplates
      type: github
      name: pirafrank/poof-azure-templates
      ref: refs/tags/v1.0.0
      endpoint: my-github-service-connection

variables:
  - group: my-variable-group   # contains GITHUB_TOKEN as a secret

steps:
  - template: poof.yml@poofTemplates
    parameters:
      version: "latest"
      packages: |
        pirafrank/vault-conductor@v0.3.0
        ms-jpq/sad
      githubTokenVariable: GITHUB_TOKEN

  - bash: |
      vault-conductor --version
      sad --version
    displayName: "Use installed tools"
```

### Pinning to a specific template version

Change the `ref` in the `resources` block:

```yaml
ref: refs/tags/v1.0.0   # exact version
```

Or follow the template's main branch:

```yaml
ref: refs/heads/main    # latest (not recommended for production)
```

### Local template

If the template is committed in your pipeline repository, reference its path directly:

```yaml
pool:
  vmImage: ubuntu-latest

steps:
  - template: .azure/templates/poof.yml
    parameters:
      packages: 'cli/cli,ms-jpq/sad'

  - bash: gh --version
```

This example assumes you copied `poof.yml` to `.azure/templates/poof.yml`.

## Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `version` | Version of poof to bootstrap (e.g. `v0.6.0`). Use `latest` for the most recent release. | `latest` |
| `packages` | Newline or comma-separated list of `USER/REPO` (or `USER/REPO@TAG`) entries to install. Leave empty to skip. | `""` |
| `githubTokenVariable` | Name of the pipeline variable holding a GitHub personal access token. Avoids API rate limiting. | `GITHUB_TOKEN` |

## Package format

```
pirafrank/vault-conductor         # latest release
pirafrank/vault-conductor@v0.3.0  # specific tag
ms-jpq/sad
```

Multiple packages can be separated by newlines or commas, with optional surrounding
whitespace. Spaces alone do not separate packages. The comments above describe the
entries; do not include inline comments in the `packages` parameter.

## Rate limiting

poof makes GitHub API calls to resolve releases. Without a token these are unauthenticated
and subject to GitHub's [rate limit](https://docs.github.com/en/rest/overview/rate-limits-for-the-rest-api)
(60 requests/hour per IP). Provide a GitHub personal access token via `githubTokenVariable` to raise
this to 5,000 requests/hour.

Store the token as a **secret pipeline variable** or in a **variable group** — never hardcode it:

```yaml
variables:
  - group: my-variable-group   # the group must contain GITHUB_TOKEN

steps:
  - template: poof.yml@poofTemplates
    parameters:
      githubTokenVariable: GITHUB_TOKEN
```

## Supported platforms

| OS | Architecture |
|----|-------------|
| Linux (glibc) | amd64 (x86_64), arm64 (aarch64) |
| Linux (musl) | amd64 (x86_64), arm64 (aarch64) |
| macOS | amd64 (Intel), arm64 (Apple Silicon) |

Use a Linux or macOS agent with Bash, curl, and tar available. For macOS hosted
agents, choose a suitable macOS image in `pool.vmImage`; for self-hosted agents,
select your Linux or macOS pool. Native Windows agents are unsupported.

The poof executable is installed to `$HOME/.local/bin`. Package binaries are exposed
through `${XDG_DATA_HOME:-$HOME/.local/share}/poof/bin` on Linux (only absolute
`XDG_DATA_HOME` values are honored), or `$HOME/Library/Application Support/poof/bin`
on macOS.

## Versioning

This template repository is versioned independently from poof itself. A new release is only
needed when the template's own behavior changes, not every time poof ships a new version
(the `version` parameter handles poof version selection).

Always pin to a specific tag in production to ensure reproducible builds.
Pin poof's `version` and package tags as well. Each invocation installs the requested
poof release, replacing any existing binary in `$HOME/.local/bin`; `latest` resolves
the current release on every run. Pipeline caching is not configured by this template.

## Development

Run the regression suite with Python 3.9+ and Bash (no additional Python packages
or GitHub credentials are required):

```sh
python3 -m unittest discover -s tests -v
```

The tests extract the Bash scripts from `poof.yml` and run them in temporary homes
with mocked GitHub requests and poof commands. They cover platform targets, package
parsing, token forwarding, version replacement, XDG paths, and failure propagation.
`azure-pipelines.yml` runs the suite on Linux and macOS. Create an Azure Pipeline
using that file to enable these checks.

These tests do not evaluate Azure template expressions or apply logging commands.
An actual Azure Pipeline run is still needed to check secret-variable mapping,
step conditions, and PATH persistence between tasks.

## License

[MIT](LICENSE.md)
