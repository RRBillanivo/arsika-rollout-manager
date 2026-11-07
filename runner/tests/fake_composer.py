"""Stand-in for composer in the tests: "install" creates vendor/autoload.php (a copy of the file named by the
environment variable FAKE_COMPOSER_AUTOLOAD) in the current folder and records its arguments."""
import os
import shutil
import sys

if len(sys.argv) > 1 and sys.argv[1] == "install":
    os.makedirs("vendor", exist_ok=True)
    shutil.copy(os.environ["FAKE_COMPOSER_AUTOLOAD"], os.path.join("vendor", "autoload.php"))
    with open(os.path.join("vendor", "composer-arguments.txt"), "w", encoding="utf-8") as f:
        f.write(" ".join(sys.argv[1:]))
    print("Installing dependencies from lock file (fake composer)")
    sys.exit(0)
print("fake composer: unknown command", " ".join(sys.argv[1:]))
sys.exit(1)
