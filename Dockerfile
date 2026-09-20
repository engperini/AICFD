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
        python3-yaml \
        python3-matplotlib \
        python3-docx \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# Fail fast at build time, on the versions THIS image has.
#
# `doctor` alone is not enough: it needs neither PyYAML nor a spec, so an image
# missing the Python layer's own dependencies built green and failed on the
# user's first real command. Each line below is a whole path exercised end to
# end instead of an import checked in isolation:
#
#   doctor   the solvers are where ADR-002 expects them
#   build    a spec is read, the geometry derived and an OpenFOAM case written
#   report   the Word deliverable is produced -- every figure rendered and the
#            document assembled, on the distribution's matplotlib and
#            python-docx rather than on the versions it was developed against
#   tests    the unit tests, which need no OpenFOAM. They include one
#            that every case in `cases/` still builds: this image carries
#            the working folder as it is, so a case edited to something
#            the generator refuses stops the build -- by design, and with
#            a message that names the file (ADR-056)
#
# It costs about a minute and it is the difference between an image that builds
# and an image that works.
RUN python3 -m aicfd doctor \
    && python3 -m aicfd build cases/pod-fanwall.yaml --out /tmp/smoke \
    && python3 -m aicfd report pod-fanwall --out /tmp/smoke.docx \
    && python3 -m unittest discover tests \
    && rm -rf /tmp/smoke /tmp/smoke.docx results

EXPOSE 8000
CMD ["python3", "-m", "aicfd", "view", "--port", "8000", "--no-browser"]
