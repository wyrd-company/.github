# relationships: validates: release-process
# Intentional 2a8385ca .github/workflows/cd.yml update-major-tag; plain stable tag trigger/build guard.
TAG="${GITHUB_REF##*/}"
MAJOR="${TAG%%.*}"
if [[ "$MAJOR" == "0" ]]; then
echo "Skipping major tag update for pre-1.0 release $TAG."
exit 0
fi
git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
git tag -fa "$MAJOR" -m "Update $MAJOR to $TAG"
git push origin "$MAJOR" --force
