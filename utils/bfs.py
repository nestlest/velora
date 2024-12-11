from collections import deque
from utils.utils import is_stablecoin
    
def breadthFirstSearch(self, token_address: str):
    queue = deque([token_address])
    prev = dict()
    visited = set()
    
    while queue:
        current_token = queue.popleft()
        if is_stablecoin(current_token):
            break
        
        visited.add(current_token)
        
        for token in self.db_manager.fetch_related_tokens(current_token):
            if token not in visited:
                queue.append(token)
                prev[token] = current_token
    
    token_pairs = []
    while current_token is not token_address:
        token_pairs.append((prev[current_token], current_token))
        current_token = prev[current_token]
    
    return token_pairs.reverse()