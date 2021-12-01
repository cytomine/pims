#!/bin/bash -x

# Replace the version /__version__.py with the one extracted from GIT TAG

echo "Parameter $1"

if [[ "$1" =~ [0-9]+.[0-9]+.[0-9]+$ ]]; then
    echo "Official release"
    PIMS_VERSION="${1//./,}" # replace "." (x.y.z) by "," (x,y,z)
    echo "Setting $PIMS_VERSION"
    sed -i -- 's/VERSION = (0, 0, 0)/VERSION = ('$PIMS_VERSION')/g' /app/pims/__version__.py ;
else
    echo "Release candidates: $1"
fi

cat /app/pims/__version__.py