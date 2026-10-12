#!/bin/sh
# Construit build/BookTranslator_Setup.exe (il faut makensis : apt install nsis)
set -e
cd "$(dirname "$0")"
rm -rf build && mkdir -p build/app/static
cp ../*.py ../requirements.txt ../LISEZMOI.md build/app/
cp ../static/* build/app/static/
makensis -V2 BookTranslator.nsi
ls -la build/BookTranslator_Setup.exe
