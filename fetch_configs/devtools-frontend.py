// GitSourceEditor.jsx
// A single-file React component that implements a browser-based Git source editor
// Uses: isomorphic-git + LightningFS for an in-browser git repo, monaco-editor for editing
// Tailwind classes are used for styling. Default export a React component.

/*
Required dependencies (install in your project):

npm install react react-dom
npm install isomorphic-git @isomorphic-git/lightning-fs
npm install monaco-editor react-monaco-editor

Notes on pushing to remotes:
- To push/fetch over HTTPS you need a CORS-enabled Git server and credentials/token.
- isomorphic-git supports http(s) with a `http` client (node-fetch in node, or browser default via callbacks).
- For production, consider adding OAuth token support and secure storage.

This component is intended as a starting point. It supports:
- Initialize / load a repo in browser FS
- File tree view (simple)
- Open/edit/save files
- Commit staged changes
- Create branches and list commits
- Basic clone/push placeholders (requires remote setup and CORS)
*/

import React, { useEffect, useState, useRef } from "react";
import * as git from "isomorphic-git";
import LightningFS from "@isomorphic-git/lightning-fs";
import MonacoEditor from "react-monaco-editor";

// Helper components inline (kept simple)
function Icon({ name }) {
  // minimal icon fallback
  return <span className="text-gray-500">{name}</span>;
}

export default function GitSourceEditor() {
  const [fs, setFs] = useState(null);
  const [dir, setDir] = useState("/repo");
  const [files, setFiles] = useState([]);
  const [currentPath, setCurrentPath] = useState(null);
  const [content, setContent] = useState("");
  const [status, setStatus] = useState("Ready");
  const [branch, setBranch] = useState("main");
  const [commits, setCommits] = useState([]);
  const [staged, setStaged] = useState([]);
  const mountedRef = useRef(false);

  // Initialize LightningFS and git repo on first mount
  useEffect(() => {
    if (mountedRef.current) return;
    mountedRef.current = true;

    async function init() {
      setStatus("Initializing filesystem...");
      const Lightning = new LightningFS("git-editor-fs", { wipe: false });
      const _fs = Lightning; // adapter compatible with isomorphic-git
      // create workdir
      try {
        await _fs.promises.mkdir(dir, { recursive: true });
      } catch (e) {
        // ignore
      }
      git.plugins.set("fs", _fs);
      setFs(_fs);

      // If a repo exists, open; otherwise init and add a sample file
      const exists = await existsDir(_fs, dir + "/.git");
      if (!exists) {
        setStatus("Creating new git repository...");
        await git.init({ dir, gitdir: dir + "/.git", defaultBranch: branch });
        await writeFile(_fs, dir + "/README.md", "# New Repo\n\nEdit files in the editor.");
        await git.add({ dir, filepath: "README.md" });
        await git.commit({ dir, message: "Initial commit", author: { name: "You", email: "you@example.com" } });
      } else {
        setStatus("Existing repo detected");
      }

      await refreshFileList(_fs);
      await refreshCommits(_fs);
      setStatus("Ready");
    }
    init();
  }, []);

  // Utilities
  async function existsDir(_fs, path) {
    try {
      const stat = await _fs.promises.stat(path);
      return stat && stat.type === "dir";
    } catch (e) {
      return false;
    }
  }

  async function writeFile(_fs, path, text) {
    await _fs.promises.writeFile(path, new TextEncoder().encode(text));
  }

  async function readFile(_fs, path) {
    try {
      const data = await _fs.promises.readFile(path);
      const text = new TextDecoder().decode(data);
      return text;
    } catch (e) {
      return "";
    }
  }

  async function refreshFileList(_fs = fs) {
    if (!_fs) return;
    setStatus("Reading files...");
    async function walk(folder) {