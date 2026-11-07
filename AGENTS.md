# Instructions for AI coding agents

## Windows and Mac must stay in sync

Every fix and feature applies to both the Windows and Mac versions of the app, and the two are kept in sync at all times. Put changes in the shared code. If something has to differ by operating system, implement and check both sides in the same change, and never ship a fix for one platform only.

Full project context, including database schemas and sync internals, is in [CLAUDE.md](CLAUDE.md). Read it before changing any code.
