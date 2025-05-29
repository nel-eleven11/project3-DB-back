from fastapi import FastAPI, Depends
from pydantic import BaseModel
from neo4j import GraphDatabase
from typing import List, Optional
from collections import deque
from fastapi.middleware.cors import CORSMiddleware 

# Neo4j connection details (update with your credentials)
NEO4J_URI = "neo4j+s://d10766eb.databases.neo4j.io"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "xg-Q7GU1M6CwfOfg5PMbLRekLX9R3fETOcJhx0ChhoQ"

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

app = FastAPI(title="Puzzle Solver API", version="1.0.0")

origins = [
    "*",  # Permite cualquier origen. Cambia a lista de orígenes permitidos para producción
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # o ['http://localhost:3000', 'https://midominio.com']
    allow_credentials=True,
    allow_methods=["*"],  # Métodos permitidos, e.g., ["GET", "POST"]
    allow_headers=["*"],  # Headers permitidos
)

class Puzzle(BaseModel):
    name: str
    total_pieces: int


class Piece(BaseModel):
    piece_id: int  # Cambiado de 'id' a 'piece_id' para claridad
    edges: List[int]
    puzzle_name: str


class Connection(BaseModel):
    piece_id: int
    edge_id: int


class Component(BaseModel):
    start_piece: int
    connections: List[Connection]


class Solution(BaseModel):
    puzzle_name: str
    components: List[Component]


def get_neo4j_session():
    try:
        session = driver.session()
        yield session
    finally:
        session.close()


def run_query(session, query, parameters=None):
    result = session.run(query, parameters or {})
    return [record.data() for record in result]


def get_edges(session, piece_id, puzzle_name):
    query = """
    MATCH (p:Piece {piece_id: $piece_id})-[:BELONGS_TO]->(puzzle:Puzzle {name: $puzzle_name})
    RETURN p.edges AS edges
    """
    result = run_query(
        session, query, {"piece_id": piece_id, "puzzle_name": puzzle_name}
    )
    return result[0]["edges"] if result else []


def find_neighbor(session, edge_code, current_piece_id, visited, puzzle_name):
    # Corregimos la consulta para usar piece_id en lugar de id autogenerado
    if not visited:
        query = """
        MATCH (p2:Piece)-[:BELONGS_TO]->(puzzle:Puzzle {name: $puzzle_name})
        WHERE $edge_code IN p2.edges 
        AND p2.piece_id <> $current_piece_id
        RETURN p2.piece_id AS neighbor_id
        LIMIT 1
        """
        result = run_query(
            session,
            query,
            {
                "edge_code": edge_code,
                "current_piece_id": current_piece_id,
                "puzzle_name": puzzle_name,
            },
        )
    else:
        query = """
        MATCH (p2:Piece)-[:BELONGS_TO]->(puzzle:Puzzle {name: $puzzle_name})
        WHERE $edge_code IN p2.edges 
        AND p2.piece_id <> $current_piece_id 
        AND NOT p2.piece_id IN $visited
        RETURN p2.piece_id AS neighbor_id
        LIMIT 1
        """
        result = run_query(
            session,
            query,
            {
                "edge_code": edge_code,
                "current_piece_id": current_piece_id,
                "visited": visited,
                "puzzle_name": puzzle_name,
            },
        )
    
    return result[0]["neighbor_id"] if result else None


def create_piece_connection(session, piece1_id, piece2_id, edge_code, puzzle_name):
    """Create a reflexive relationship between two pieces"""
    query = """
    MATCH (p1:Piece {piece_id: $piece1_id})-[:BELONGS_TO]->(puzzle:Puzzle {name: $puzzle_name})
    MATCH (p2:Piece {piece_id: $piece2_id})-[:BELONGS_TO]->(puzzle)
    MERGE (p1)-[:CONNECTS_TO {edge_code: $edge_code}]->(p2)
    MERGE (p2)-[:CONNECTS_TO {edge_code: $edge_code}]->(p1)
    """
    session.run(
        query,
        {
            "piece1_id": piece1_id,
            "piece2_id": piece2_id,
            "edge_code": edge_code,
            "puzzle_name": puzzle_name,
        },
    )


def solve_puzzle(session, puzzle_name: str, start_piece_id: Optional[int] = None):
    # Get all pieces for this puzzle
    all_pieces_query = """
    MATCH (p:Piece)-[:BELONGS_TO]->(puzzle:Puzzle {name: $puzzle_name})
    RETURN p.piece_id AS piece_id
    """
    all_pieces = [
        record["piece_id"]
        for record in run_query(session, all_pieces_query, {"puzzle_name": puzzle_name})
    ]

    if not all_pieces:
        return Solution(puzzle_name=puzzle_name, components=[])

    if start_piece_id is None or start_piece_id not in all_pieces:
        start_piece_id = all_pieces[0]

    visited = set()
    components = []

    def assemble_component(start_piece):
        queue = deque([start_piece])
        visited.add(start_piece)
        connections = []

        while queue:
            current_piece = queue.popleft()
            edges = get_edges(session, current_piece, puzzle_name)
            for edge in edges:
                neighbor_id = find_neighbor(
                    session, edge, current_piece, list(visited), puzzle_name
                )
                if neighbor_id:
                    # Create the connection relationship in Neo4j
                    create_piece_connection(
                        session, current_piece, neighbor_id, edge, puzzle_name
                    )

                    connections.append(Connection(piece_id=neighbor_id, edge_id=edge))
                    visited.add(neighbor_id)
                    queue.append(neighbor_id)

        return Component(start_piece=start_piece, connections=connections)

    # Start with the specified piece or first available
    components.append(assemble_component(start_piece_id))

    # Handle remaining disconnected components
    remaining = set(all_pieces) - visited
    while remaining:
        next_start_piece = remaining.pop()
        components.append(assemble_component(next_start_piece))
        remaining = set(all_pieces) - visited

    return Solution(puzzle_name=puzzle_name, components=components)


@app.post("/api/puzzle")
def create_puzzle(puzzle: Puzzle, session=Depends(get_neo4j_session)):
    """Create a new puzzle node"""
    query = """
    MERGE (puzzle:Puzzle {name: $name})
    SET puzzle.total_pieces = $total_pieces
    """
    session.run(query, name=puzzle.name, total_pieces=puzzle.total_pieces)
    return {"message": f"Puzzle '{puzzle.name}' created successfully"}


@app.post("/api/piece")
def add_piece(piece: Piece, session=Depends(get_neo4j_session)):
    """Add a piece to a specific puzzle"""
    # First, ensure the puzzle exists
    puzzle_check_query = "MATCH (puzzle:Puzzle {name: $puzzle_name}) RETURN puzzle"
    puzzle_exists = run_query(session, puzzle_check_query, {"puzzle_name": piece.puzzle_name})
    
    if not puzzle_exists:
        return {"error": f"Puzzle '{piece.puzzle_name}' does not exist"}

    # Check if piece_id already exists in this puzzle
    piece_check_query = """
    MATCH (p:Piece {piece_id: $piece_id})-[:BELONGS_TO]->(puzzle:Puzzle {name: $puzzle_name})
    RETURN p
    """
    piece_exists = run_query(
        session, 
        piece_check_query, 
        {"piece_id": piece.piece_id, "puzzle_name": piece.puzzle_name}
    )
    
    if piece_exists:
        return {"error": f"Piece with ID {piece.piece_id} already exists in puzzle '{piece.puzzle_name}'"}

    # Create the piece with auto-generated internal ID and relationship
    query = """
    MATCH (puzzle:Puzzle {name: $puzzle_name})
    CREATE (p:Piece {piece_id: $piece_id, edges: $edges})
    CREATE (p)-[:BELONGS_TO]->(puzzle)
    RETURN id(p) AS internal_id
    """
    result = session.run(
        query, 
        piece_id=piece.piece_id, 
        edges=piece.edges, 
        puzzle_name=piece.puzzle_name
    )
    
    internal_id = result.single()["internal_id"]
    
    return {
        "message": "Piece added successfully",
        "piece_id": piece.piece_id,
        "internal_id": internal_id,
        "puzzle_name": piece.puzzle_name
    }


@app.get("/api/solution/{puzzle_name}")
def get_solution(
    puzzle_name: str,
    start_piece_id: Optional[int] = None,
    session=Depends(get_neo4j_session),
):
    """Get the solution for a specific puzzle with optional starting piece"""
    solution = solve_puzzle(session, puzzle_name, start_piece_id)
    return solution


@app.get("/api/puzzles")
def list_puzzles(session=Depends(get_neo4j_session)):
    """List all available puzzles"""
    query = """
    MATCH (puzzle:Puzzle)
    OPTIONAL MATCH (p:Piece)-[:BELONGS_TO]->(puzzle)
    RETURN puzzle.name AS name, 
           puzzle.total_pieces AS total_pieces, 
           count(p) AS current_pieces
    """
    puzzles = run_query(session, query)
    return {"puzzles": puzzles}


@app.get("/api/puzzle/{puzzle_name}/pieces")
def get_puzzle_pieces(puzzle_name: str, session=Depends(get_neo4j_session)):
    """Get all pieces for a specific puzzle"""
    query = """
    MATCH (p:Piece)-[:BELONGS_TO]->(puzzle:Puzzle {name: $puzzle_name})
    RETURN p.piece_id AS piece_id, p.edges AS edges, id(p) AS internal_id
    ORDER BY p.piece_id
    """
    pieces = run_query(session, query, {"puzzle_name": puzzle_name})
    return {"puzzle_name": puzzle_name, "pieces": pieces}


@app.delete("/api/cleanup")
def cleanup_database(session=Depends(get_neo4j_session)):
    """Clean up all data (use with caution!)"""
    query = "MATCH (n) DETACH DELETE n"
    session.run(query)
    return {"message": "Database cleaned successfully"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9080)

