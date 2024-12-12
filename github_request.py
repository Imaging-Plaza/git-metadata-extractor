from github import Github, Auth


class GithubRequest:
    def __init__(self, token):
        self.auth = Auth.Token(token)
        self.g = Github(auth=self.auth)

    # request all repos url from an organisation
    def request_repos(self, organisation):
        repos = []
        org = self.g.get_organization(organisation)
        for repo in org.get_repos():
            repos.append(repo.html_url)
        return repos

    # request all repos infos from an organisation
    def request_repos_infos(self, organisation):
        repos = []
        org = self.g.get_organization(organisation)
        for repo in org.get_repos():
            repos.append(self.fetch_repo_infos(repo))
        return repos
    
    # request infos of a specific repo name
    def request_repo_infos(self, repo_name):
        repo = self.g.get_repo(repo_name)
        repo_dict = self.fetch_repo_infos(repo)
        return repo_dict
    
    # def fetch_repo_infos(self, repo):
    #     readme = None
    #     contents = repo.get_contents("")
    #     for content_file in contents:
    #         if content_file.path == "README.md":
    #             readme = repo.html_url + "/blob/" + repo.default_branch + "/README.md"

    #     last_commit_date = None
    #     last_commit_sha = repo.get_branch(repo.default_branch).commit.sha
    #     last_commit_date = repo.get_commit(sha=last_commit_sha).commit.author.date

    #     repo_dict = {"url": repo.html_url, "topics": repo.get_topics(), "readme": readme, "last_commit_date": last_commit_date,
    #                  "description": repo.description, "created_at": repo.created_at, "language": repo.language}
    #                     #, "topics": repo.get_topics(), 
    #                      #"collaborators": repo.get_collaborators(),
    #                      #"contributors": repo.get_contributors()}
    #     return repo_dict

    def fetch_repo_infos(self, repo):
        repo_dict = {
            "url": repo.html_url,
            "description": repo.description,
            "created_at": repo.created_at,
            "language": repo.language,
            "owner": {
                "login": repo.owner.login,
                "url": repo.owner.html_url
            },
            "topics": repo.get_topics(),
            "license": repo.license.name if repo.license else None
        }
        
        readme_url = None
        try:
            contents = repo.get_contents("")
            for content_file in contents:
                if content_file.path == "README.md":
                    readme_url = f"{repo.html_url}/blob/{repo.default_branch}/README.md"
        except:
            pass
        
        try:
            last_commit_sha = repo.get_branch(repo.default_branch).commit.sha
            last_commit = repo.get_commit(sha=last_commit_sha)
            last_commit_date = last_commit.commit.author.date
        except:
            last_commit_date = None

        repo_dict.update({
            "readme": readme_url,
            "last_commit_date": last_commit_date
            })
        
        contributors = []
        try:
            for contrib in repo.get_contributors():
                contributors.append(contrib.login)
            
            repo_dict["contributors"] = contributors
        except:
            repo_dict["contributors"] = []

        collaborators = []
        try:
            for collab in repo.get_collaborators():
                collaborators.append(collab.login)
            
            repo_dict["collaborators"] = collaborators
        except:
            repo_dict["collaborators"] = []

        return repo_dict

    # close the connection
    def close(self):
        self.g.close()

