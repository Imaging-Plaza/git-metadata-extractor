"""Extract a GitHub user and related entities through the V2 pipeline."""

from examples._extract import main

if __name__ == "__main__":
    main("https://github.com/octocat", "user.json")
