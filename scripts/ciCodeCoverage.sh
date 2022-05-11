#!/bin/bash

set -o xtrace
set -o errexit

echo "************************************** Launch tests ******************************************"

file='./ci/version'
VERSION_NUMBER=$(<"$file")

echo "Launch code coverage for $VERSION_NUMBER"
mkdir "$PWD"/ci/code-coverage-reports
#mkdir "$PWD"/ci/code-coverage-reports
docker build --rm -f scripts/docker/Dockerfile-code-coverage.build --build-arg VERSION_NUMBER=$VERSION_NUMBER -t  cytomine/pims-code-coverage .

containerId=$(docker create -v "$PWD"/ci/code-coverage-reports:/app/ci/code-coverage-reports  -v /tmp/uploaded:/tmp/uploaded -v /data/pims:/data/pims cytomine/pims-code-coverage )

docker start -ai  $containerId
docker rm $containerId
