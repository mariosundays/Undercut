# Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io),
certificate by [SignPath Foundation](https://signpath.org).

## What is signed

Only the Windows release files that GitHub Actions builds from this
repository: `Undercut.exe` and the `Undercut_vX.Y.Z_setup.exe` installer
attached to each [release](https://github.com/mariosundays/Undercut/releases).
They are built by [`.github/workflows/release.yml`](.github/workflows/release.yml)
from the tagged source. Nothing built on a personal machine is ever signed.

## Team roles

- Committers and reviewers: [Mario Domingos](https://github.com/mariosundays)
- Approvers: [Mario Domingos](https://github.com/mariosundays)

Every signing request is approved by hand before it is signed.

## Privacy policy

Undercut works on your video files locally. It does not collect, store or
send any personal data or usage data.

The one exception is the update check. The installed app asks GitHub's public
releases API (`api.github.com`) whether a newer version exists, and downloads
the installer from GitHub if you agree to update. That request contains no
information about you or your files. Builds run from source never check.
