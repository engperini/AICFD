# AICFD runtime: OpenFOAM + the Python layer + the static viewer.
#
# OpenFOAM v1912 comes from the Ubuntu 24.04 archive. Its shell helpers are
# incomplete (see docs/DECISIONS.md, ADR-002), which is why nothing here sources
# etc/bashrc -- aicfd.run supplies a clean environment per command instead.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update -qq && apt-get install -y --no-install-recommends \
        openfoam \
        openfoam-examples \
        python3 \
        python3-numpy \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# Fail fast at build time if the solvers are not where ADR-002 expects them.
RUN python3 -m aicfd doctor

EXPOSE 8000
CMD ["python3", "-m", "aicfd", "view", "--port", "8000", "--no-browser"]
