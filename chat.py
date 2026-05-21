import os
from dotenv import load_dotenv
from chatbot.conversation import AteneoChat

load_dotenv()

if not os.environ.get("ANTHROPIC_API_KEY"):
    raise SystemExit("Falta ANTHROPIC_API_KEY en el archivo .env")


def main():
    chat = AteneoChat()
    courses = chat.list_courses()

    print("=" * 45)
    print("  Atenea — Asistente de Estudio Socrático")
    print("=" * 45)

    if not courses:
        raise SystemExit("\nNo hay cursos indexados. Ejecuta primero: python ingest.py")

    print("\nCursos disponibles:")
    for i, c in enumerate(courses, 1):
        print(f"  {i}. {c}")
    print("  0. Todos los cursos")

    choice = input("\nSelecciona un curso (número): ").strip()
    course = None
    if choice.isdigit() and 1 <= int(choice) <= len(courses):
        course = courses[int(choice) - 1]
        print(f"\nCurso: {course}")
    else:
        print("\nBuscando en todos los cursos.")

    print("\nEscribe tu pregunta. 'salir' para terminar.\n")

    while True:
        user_input = input("Tú: ").strip()
        if not user_input:
            continue
        if user_input.lower() in ("salir", "exit", "quit"):
            break
        response = chat.chat(user_input, course=course)
        print(f"\nAtenea: {response}\n")

    log_path = chat.save_session()
    print(f"\nSesión guardada en: {log_path}")
    print("Ejecuta 'python analysis.py' para ver tu reporte de estudio.")


if __name__ == "__main__":
    main()
