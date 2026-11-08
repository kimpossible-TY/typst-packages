# Reviewed package test inputs

These files are immutable test inputs copied from the public typst-packages
repository at commit `96e087b18de6f931e624a8b81bfffbc1ccb54a47` (and its
ancestors). They are not package updates or evidence about a consumer's
installed packages. The text-utils entrypoint SHA-256 is
`7e151150dac47b01ebf7706457906133d74a52abee1b5c43ce824ff89279bd71`.

Tests exercise the exact reviewed contracts against these copies so changes to
packages on the pull request's base branch cannot silently change their inputs.
Unrecognized package bytes remain rejected by the production exact-hash checks.
A different reviewed text-utils source may supply collision-only namespace
evidence without acquiring this fixture's helper identities.

`variants/text-utils-670599.typ` is the public entrypoint at commit
`c161707d760756d12fe998345dae81e2a36308c1`. Its SHA-256 is
`670599f5fca254fdb93487021099678f13ecb6357877e93ebdcf6cc3e17992ed`.
It is used only to verify collision-only handling and rejection as the older
reviewed helper; it was retrieved from that Git commit, not a user installation.
