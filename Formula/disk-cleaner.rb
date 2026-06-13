# Homebrew formula for Disk Cleaner.
#
# Lives in the tap repo (prateek4506/homebrew-tap) at Formula/disk-cleaner.rb, so:
#     brew install prateek4506/tap/disk-cleaner
#
# To cut a new version: tag a release on the disk-cleaner repo, then update
# `url` + `sha256` here (sha256 via: curl -sL <tarball-url> | shasum -a 256).
class DiskCleaner < Formula
  desc "macOS disk cleaner that explains each file with AI before you delete it"
  homepage "https://github.com/prateek4506/disk-cleaner"
  url "https://github.com/prateek4506/disk-cleaner/archive/refs/tags/v0.2.0.tar.gz"
  sha256 "a0fa21277357d60cef5941d28ba2a6fde94c132de31d6e84f39416e34e4b3254"
  license "MIT"
  head "https://github.com/prateek4506/disk-cleaner.git", branch: "main"

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
