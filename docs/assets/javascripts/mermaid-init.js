(function () {
  const renderMermaid = () => {
    if (typeof mermaid === "undefined") {
      return;
    }

    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "loose",
      theme: "default",
    });

    mermaid.run({
      querySelector: ".mermaid",
    });
  };

  if (typeof document$ !== "undefined" && document$.subscribe) {
    document$.subscribe(renderMermaid);
  } else {
    window.addEventListener("load", renderMermaid);
  }
})();
