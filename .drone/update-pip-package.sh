#!/bin/sh

set -e

apk add --update --no-cache py3-twine py3-setuptools py3-wheel py3-pip py3-build
rm -rf build dist
python -m build
twine upload dist/songhive-*
