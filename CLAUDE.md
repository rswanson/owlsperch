# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

`owlsperch` is a newly initialized repository. As of the initial commit it contains only a `README.md` with the project name. There is no source code, build system, package manifest, test runner, or CI configuration yet.

Because nothing has been chosen, do not assume a language or toolchain. Check the repo root for a manifest (`Cargo.toml`, `package.json`, `pyproject.toml`, `go.mod`, etc.) before running any build, lint, or test command, and update this file with the real commands once they exist.

## Keeping this file useful

When the project takes shape, replace the "Current state" section with:

- The commands to build, lint, run tests, and run a single test.
- The high-level architecture: the main entry points, how the major modules relate, and any non-obvious conventions that require reading several files to understand.
