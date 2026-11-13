# Execution environment for the pilot repository: interpreter and test dependencies only.
# The repository is mounted at run time; containers get no network access.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# Behind a TLS-intercepting proxy the builder may offer its CA bundle as the
# build secret "ca_bundle". It is used for this step only and is not stored.
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    pip install --no-cache-dir --disable-pip-version-check pytest==8.3.4
