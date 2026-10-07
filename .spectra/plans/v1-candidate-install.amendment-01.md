# A01 — missing installed CLI version option

Authorization: root requested the narrow prospective repair after actual frozen C1 fd5ee46 wheel installation succeeded but magicite --version returned exit2 / No such option. Help and typed doctor succeeded. No source change by RAMZA.

Intent: the accepted candidate-install plan's first-use flow explicitly requires version/help. Base production CLI had no version option; do not describe this as regression of an existing supported command. Add standard Click metadata-backed version_option(package_name="magicite") in src/magicite/__main__.py and a real CLI/installed-metadata regression in tests/unit/test_cli_version.py. Existing probe scope also allows persisting command diagnostics before validation.

Scope additions: src/magicite/__main__.py and tests/unit/test_cli_version.py. Rightsize remains lite (estimated10files + security + medium stakes =4). All ten criteria remain exactly 7e5d7bbf570378a206ed4f841a2c4788d55cfbc03e19b6c01a46a000e6c13e51. No version bump, publication, security acceptance or other production API change.

C1 failed attempt remains immutable. New production bytes require new clean candidate C2, newly built wheel/sdist, both actual installation/probe rows and normal independent review/CI. No retroactive pass, report rewrite or old-artifact relabeling.
