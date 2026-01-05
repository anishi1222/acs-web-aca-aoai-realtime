#!/bin/bash

# WebでNode.js環境をセットアップして起動

cd web

# Prefer reproducible installs.
if [ -f package-lock.json ]; then
	npm ci
else
	npm i
fi
npm run dev
