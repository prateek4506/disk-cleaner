# Homebrew formula for Disk Cleaner.
#
# To publish via a tap:
#   1) Create a release/tag on GitHub (e.g. v0.1.0).
#   2) Set `url` to that tag's tarball and fill in `sha256`
#      (get it with: `curl -sL <tarball-url> | shasum -a 256`).
#   3) Put this file in a `homebrew-tap` repo at Formula/disk-cleaner.rb, then:
#         brew install prateek4506/tap/disk-cleaner
#
# Until a release exists, install with the curl one-liner or `brew install --HEAD` below.
class DiskCleaner < Formula
  desc "macOS disk cleaner that explains each file with AI before you delete it"
  homepage "https://github.com/prateek4506/disk-cleaner"
  license "MIT"
  head "https://github.com/prateek4506/disk-cleaner.git", branch: "main"

  # Released versions (uncomment + fill sha256 after tagging a release):
  # url "https://github.com/prateek4506/disk-cleaner/archive/refs/tags/v0.1.0.tar.gz"
  # sha256 "FILL_ME_IN"
  # version "0.1.0"

  depends_on "python@3.12" => :recommended

  def install
    libexec.install Dir["*"]
    (bin/"disk-cleaner").write <<~SH
      #!/bin/bash
      exec /usr/bin/env python3 "#{libexec}/bin/disk-cleaner" "$@"
    SH
  end

  test do
    # Suggestion-only run on a temp dir must exit cleanly.
    system bin/"disk-cleaner", "--path", testpath, "--min-size", "999999"
  end
end
