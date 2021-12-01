#!/bin/bash

set -o xtrace
set -o errexit

echo "************************************** Create dependency image ******************************************"

file='./ci/version'
VERSION_NUMBER=$(<"$file")

echo "Launch Create dependency image for $VERSION_NUMBER"

docker build --rm -f scripts/docker/Dockerfile-dependencies.build -t  cytomine/pims-dependencies:v$VERSION_NUMBER .