#!/bin/sh

. .drone/macros/configure-git.sh
. .drone/macros/configure-ssh.sh

ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null

git remote add github git@github.com:/blacklight/songhive.git

# Push the current repository state to the GitHub mirror
git push -f --all -v github
git push --tags -v github
