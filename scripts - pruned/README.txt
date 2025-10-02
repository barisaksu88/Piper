README for scripts - pruned

Purpose:This folder mirrors the structure of scripts/, but only contains unused or archived files/functions moved during pruning. It acts as a ledger of what was stripped, so the main scripts/ tree remains clean for:

KGB snapshots

Analyzer runs

ChatGPT audits

Rules:

Never import or run code from scripts - pruned.

Exclude it explicitly from analyzers (--exclude "*/scripts - pruned/*").

Use it only as a reference to see what was removed and where it originally lived.

All archived code is still accessible in */old/* subfolders inside the real tree, but this folder centralizes visibility.