# Hoosegow: toad in a hole for agents

## Use case

Users of command-line claude and codex who want file system and kernel isolation from agent shenanigans.

Suppose all you want to do is stuff your agents in an isolation chamber where they cannot see anything you don't want them to.
Suppose you have to do this because they jump out of any limits you give them, every day.
Suppose you use Claude, Codex, and Gemini.  Opencode, too, possibly.
Suppose you use them from the terminal, mostly.

## Approach

The implementation path is a simplified version of Bullpen without tickets, workers, commits, stats, or hair.

Re-use Bullpen's sandbox setup and terminal technology but substitute a really nice web-based terminal manager for bullpen.

Re-use Bullpen Manager, but simplify it and have it include the terminal web UI against all the locally configured sandboxes.

End up with a single web server executable that sets up sandboxes and mediates terminal traffic in/out.

## User experience

Start the server
Click to create a sandbox against a workspace root
Click to create a terminal in the sandbox
There is no step 4.

