from github import Github, Auth


class GithubRequest:
    def __init__(self, token):
        self.auth = Auth.Token(token)

    def request_repos(self, organisation):
        repos = []
        g = Github(auth=self.auth)
        org = g.get_organization(organisation)
        for repo in org.get_repos():
            repos.append(repo.html_url)
        g.close()
        return repos

