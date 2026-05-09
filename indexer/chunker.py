import importlib
import re
from pathlib import Path

from tree_sitter import Parser

LANGUAGE_MODULES = {
    'csharp': 'tree_sitter_c_sharp',
    'javascript': 'tree_sitter_javascript',
}

METHOD_NODE_TYPES = {
    'csharp': {
        'method_declaration',
        'constructor_declaration',
        'local_function_statement',
    },
    'javascript': {
        'function_declaration',
        'function_expression',
        'arrow_function',
        'method_definition',
        'generator_function',
    },
}

TOKEN_PATTERN = re.compile(r"\w+|[^\s\w]+", re.UNICODE)


def determine_language(extension: str) -> str | None:
    if extension == '.cs' or extension == '.cshtml':
        return 'csharp'
    if extension == '.js':
        return 'javascript'
    return None


def load_language(language: str):
    module_name = LANGUAGE_MODULES.get(language)
    if module_name is None:
        raise ValueError(f'Unsupported language: {language}')

    module = importlib.import_module(module_name)
    
    # Новый API tree-sitter 0.25+
    if hasattr(module, 'language'):
        from tree_sitter import Language
        return Language(module.language())
    
    # Старый API
    for attr in ('LANGUAGE',):
        if hasattr(module, attr):
            return getattr(module, attr)

    raise AttributeError(
        f'Could not find a Language object in package {module_name}.'
    )

def build_parser(language: str) -> Parser:
    return Parser(load_language(language))


def get_node_text(node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='replace')


def byte_offset_from_char_index(text: str, char_index: int) -> int:
    return len(text[:char_index].encode('utf-8'))


def line_for_byte_offset(source_bytes: bytes, byte_offset: int) -> int:
    return source_bytes[:byte_offset].decode('utf-8', errors='replace').count('\n') + 1


def find_method_nodes(root, language: str):
    node_types = METHOD_NODE_TYPES[language]
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in node_types:
            yield node
        stack.extend(reversed(node.children))


def find_name_node(node):
    by_field = node.child_by_field_name('name')
    if by_field is not None:
        return by_field

    for child in node.children:
        if child.type in {'identifier', 'property_identifier', 'field_identifier', 'name'}:
            return child
        result = find_name_node(child)
        if result is not None:
            return result
    return None


def extract_method_name(node, source_bytes: bytes) -> str:
    name_node = find_name_node(node)
    if name_node is not None:
        return get_node_text(name_node, source_bytes).strip() or '<anonymous>'
    return '<anonymous>'


def tokenize_method(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in TOKEN_PATTERN.finditer(text)]


def build_chunks_for_node(node, source_bytes: bytes, file_path: str, language: str) -> list[dict]:
    node_text = get_node_text(node, source_bytes)
    token_spans = tokenize_method(node_text)
    method_name = extract_method_name(node, source_bytes)
    chunks = []

    def make_chunk(span_start: int, span_end: int, start_token_index: int, end_token_index: int):
        chunk_text = node_text[span_start:span_end]
        chunk_start_byte = node.start_byte + byte_offset_from_char_index(node_text, span_start)
        chunk_end_byte = node.start_byte + byte_offset_from_char_index(node_text, span_end)
        return {
            'file': str(file_path),
            'language': language,
            'method_name': method_name,
            'start_line': line_for_byte_offset(source_bytes, chunk_start_byte),
            'end_line': line_for_byte_offset(source_bytes, chunk_end_byte),
            'content': chunk_text,
            'token_count': end_token_index - start_token_index,
        }

    total_tokens = len(token_spans)
    if total_tokens == 0:
        chunks.append(make_chunk(0, len(node_text), 0, 0))
        return chunks

    if total_tokens <= 300:
        chunk = make_chunk(0, len(node_text), 0, total_tokens)
        chunks.append(chunk)
        return chunks

    overlap = 50
    window = 300
    start_index = 0
    while start_index < total_tokens:
        end_index = min(start_index + window, total_tokens)
        span_start = token_spans[start_index][0]
        span_end = token_spans[end_index - 1][1]
        chunk = make_chunk(span_start, span_end, start_index, end_index)
        chunks.append(chunk)
        if end_index == total_tokens:
            break
        start_index += window - overlap

    return chunks


def merge_small_chunks(chunks: list[dict]) -> list[dict]:
    if not chunks:
        return []

    merged = [chunks[0]]
    for chunk in chunks[1:]:
        if chunk['token_count'] < 50:
            prev = merged[-1]
            joined_content = prev['content'] + '\n' + chunk['content']
            merged[-1] = {
                'file': prev['file'],
                'language': prev['language'],
                'method_name': f"{prev['method_name']} + {chunk['method_name']}",
                'start_line': prev['start_line'],
                'end_line': chunk['end_line'],
                'content': joined_content,
                'token_count': prev['token_count'] + chunk['token_count'],
            }
        else:
            merged.append(chunk)

    if len(merged) > 1 and merged[0]['token_count'] < 50:
        first, second, *rest = merged
        merged = [
            {
                'file': first['file'],
                'language': first['language'],
                'method_name': f"{first['method_name']} + {second['method_name']}",
                'start_line': first['start_line'],
                'end_line': second['end_line'],
                'content': first['content'] + '\n' + second['content'],
                'token_count': first['token_count'] + second['token_count'],
            }
        ] + rest

    for chunk in merged:
        chunk.pop('token_count', None)
    return merged


def chunk_source(file_path: str, language: str) -> list[dict]:
    source_bytes = Path(file_path).read_bytes()
    parser = build_parser(language)
    tree = parser.parse(source_bytes)
    root = tree.root_node

    chunks = []
    for method_node in find_method_nodes(root, language):
        chunks.extend(build_chunks_for_node(method_node, source_bytes, file_path, language))

    return merge_small_chunks(chunks)


def chunk_code_from_string(code: str, language: str, file_path: str = '<unknown>') -> list[dict]:
    source_bytes = code.encode('utf-8')
    parser = build_parser(language)
    tree = parser.parse(source_bytes)
    root = tree.root_node

    chunks = []
    for method_node in find_method_nodes(root, language):
        chunks.extend(build_chunks_for_node(method_node, source_bytes, file_path, language))

    return merge_small_chunks(chunks)


if __name__ == '__main__':
    from pathlib import Path as _Path

    path = _Path('example.cs')
    if path.exists():
        print(chunk_source(str(path), 'csharp'))
