(function (global) {
  function mount(options) {
    const status = document.getElementById(options.statusId);
    const download = document.getElementById(options.downloadId);
    const preview = document.getElementById(options.previewId);
    const versionUrl = options.versionUrl || "version.json";
    const pdfUrl = options.pdfUrl || "main.pdf";
    const sourceUrl = options.sourceUrl || "https://github.com/kimpossible-TY/Partial-Differential-Equations";
    const params = new URLSearchParams(window.location.search);
    const checkedBranch = params.get("pdfBranch");
    const checkedBuiltAt = params.get("pdfBuiltAt");

    function setStatus(message, link) {
      status.textContent = message;
      if (!link) return;
      status.append(" ");
      const anchor = document.createElement("a");
      anchor.href = link.href;
      anchor.target = "_blank";
      anchor.rel = "noopener";
      anchor.textContent = link.text;
      status.append(anchor);
    }

    fetch(versionUrl + "?cacheBust=" + Date.now())
      .then((response) => {
        if (!response.ok) throw new Error("version metadata unavailable");
        return response.json();
      })
      .then((version) => {
        const latestVersionId = version.versionId || `${version.branch}@${version.builtAt}`;
        const checkedVersionId = `${checkedBranch}@${checkedBuiltAt}`;
        const cacheBustedPdfUrl = pdfUrl + "?v=" + encodeURIComponent(latestVersionId);
        const hasVersionCheck = Boolean(checkedBranch && checkedBuiltAt);
        const versionMatches = checkedBranch === version.branch && checkedBuiltAt === version.builtAt;

        if (download) download.href = cacheBustedPdfUrl;
        if (preview) preview.src = cacheBustedPdfUrl;

        if (hasVersionCheck) {
          if (versionMatches) {
            status.classList.add("is-current");
            status.classList.remove("is-outdated");
            setStatus(`This PDF is current. ${latestVersionId}.`, { href: sourceUrl, text: "Source" });
          } else {
            status.classList.add("is-outdated");
            status.classList.remove("is-current");
            setStatus(`This PDF is outdated. Your PDF: ${checkedVersionId}. Latest: ${latestVersionId}.`, {
              href: cacheBustedPdfUrl,
              text: "Download latest PDF",
            });
          }
        } else {
          status.classList.remove("is-current", "is-outdated");
          setStatus(`Latest published PDF version: ${latestVersionId}`, { href: sourceUrl, text: "Source" });
        }
      })
      .catch(() => {
        status.textContent = "Latest published PDF version: unavailable";
      });
  }

  global.PdfVersionCheck = { mount };
})(window);
