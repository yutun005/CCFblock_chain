# Third-party dependencies

Project code is provided under the root LICENSE. Dependencies retain their own licenses.

| Dependency | Version | Source | License |
|---|---|---|---|
| OpenZeppelin Contracts | 5.2.0 | https://github.com/OpenZeppelin/openzeppelin-contracts | MIT |
| Forge Standard Library | 1.9.7 | https://github.com/foundry-rs/forge-std | MIT or Apache-2.0 |

`toolchain-lock.json` pins immutable commits and archive SHA-256 checksums. `python manage.py setup` installs source and upstream license files in the Git-ignored `contracts/lib/` directory. They are not copied into this source repository.

Python and JavaScript versions are pinned in `requirements.lock` and `apps/web/pnpm-lock.yaml`; their packages include upstream licensing information. Redistributed offline environments or dependency bundles must retain the applicable notices and licenses. Foundry and container base images retain upstream licenses as well.

The Git commit history preserves the source revision used for each release.
