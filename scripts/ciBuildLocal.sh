#!/bin/bash

set -o xtrace
set -o errexit
set -a

rm -rf ./ci
mkdir ./ci

./scripts/ciBuildVersion.sh

./scripts/ciCreateDependencyImage.sh

./scripts/ciTest.sh

./scripts/ciCodeCoverage.sh

./scripts/ciBuildDockerImage.sh
