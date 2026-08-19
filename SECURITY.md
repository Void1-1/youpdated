# Security policy

## Supported versions

The latest release on [PyPI](https://pypi.org/project/youpdated/) and the `main` branch. This is a pre-1.0 project; fixes land in a new release rather than being backported.

## Reporting a vulnerability

Use [GitHub's private vulnerability reporting](https://github.com/Void1-1/youpdated/security/advisories/new) rather than a public issue. Please don't include tokens or credentials in the report.

Expect an acknowledgement within a week or so. This is a small project maintained in spare time and there is no guaranteed response window.

## What's in scope

Youpdated is a local CLI that reads public endpoints. The interesting attack surface is narrow but real:

- **Untrusted response content.** Every source parses data from a remote server. Feed titles,
  release notes, and HTML are attacker-influenced if a watched service is compromised. Anything
  that turns that content into code execution, a path outside the config/data directories, or a
  request to an unintended host is in scope.
- **Privacy leaks.** Youpdated promises that when `privacy.proxy` is set, *every* request goes
  through it, and that nothing is sent anywhere except the configured sources. A path that bypasses
  the proxy, leaks the config or history off the machine, or attaches identifying data to requests
  is a vulnerability, not just a bug. This includes failing *open*: if the proxy is unreachable, no
  request may fall back to a direct connection. It also includes resolving a watched hostname
  locally instead of handing it to the proxy, which would leak the whole watch list to your DNS
  resolver even though the requests themselves are proxied.
- **Credential handling.** `GITHUB_TOKEN` and `YOUTUBE_API_KEY` are read from the environment and
  must never be written to disk, logged, or sent to any host other than the one they belong to.
- **`youpdated uninstall`.** It deletes files. Any way to make it remove something outside its own
  config and data directories is in scope.
- **Encryption at rest.** When the config or history is encrypted, the passphrase and the decrypted
  contents must exist only in memory. Anything that writes plaintext to disk, logs the passphrase,
  accepts a file whose authentication tag does not verify, or lets the KDF parameters be weakened
  is in scope. So is a way to read an encrypted file's contents without the passphrase.

## What's out of scope

- A watched service returning wrong or malicious data that is merely displayed as text. Report
  it as a bug if the output is mangled.
- Rate limiting or blocking by an upstream service (YouTube's feed does this intermittently by
  design: see the README).
- Requiring a proxy or Tor to be running. Youpdated does not start or manage one.
- Anything reachable while Youpdated is running with an unlocked setup: the passphrase is in the
  process's memory by necessity, and `YOUPDATED_PASSPHRASE` is readable by other processes of the
  same user. Encryption at rest protects the files, not a live session.
- Plaintext left in free space by `youpdated encrypt`. It overwrites the file; recovering the old
  blocks from the underlying device is out of scope, and not something a userspace tool can
  reliably prevent on modern storage.
- Vulnerabilities in dependencies with no exploitable path through this code. Report those upstream;
  Dependabot tracks version bumps here.
