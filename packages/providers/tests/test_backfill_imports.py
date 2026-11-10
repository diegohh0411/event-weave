import subprocess
import sys


def test_scheduler_imports_without_crawl_extra():
    # The API image has no `crawl` extra but imports the scheduler for "Check now".
    code = ("import sys; sys.modules['defusedxml'] = None; sys.modules['feedparser'] = None; "
            "import weave_providers.crawl.scheduler, weave_providers.crawl.backfill")
    subprocess.run([sys.executable, "-c", code], check=True)
