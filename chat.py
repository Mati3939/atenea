import os
from dotenv import load_dotenv
from chatbot.conversation import AteneoChat

load_dotenv()


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

        units = chat.store.list_files_in_collection(course)
        unit = None
        if units:
            print("\nUnidades disponibles:")
            for i, u in enumerate(units, 1):
                print(f"  {i}. {u}")
            print("  0. Todas las unidades")
            uc = input("\nSelecciona una unidad (número): ").strip()
            if uc.isdigit() and 1 <= int(uc) <= len(units):
                unit = units[int(uc) - 1]
                print(f"Unidad: {unit}")
    else:
        print("\nBuscando en todos los cursos.")
        unit = None

    print("\nEscribe tu pregunta. 'salir' para terminar.\n")

    while True:
        user_input = input("Tú: ").strip()
        if not user_input:
            continue
        if user_input.lower() in ("salir", "exit", "quit"):
            break
        result = chat.chat(user_input, course=course, unit=unit)
        print(f"\nAtenea: {result['text']}\n")
        if result.get("options"):
            print("  Opciones: " + " | ".join(result["options"]) + "\n")

    log_path = chat.save_session()
    print(f"\nSesión guardada en: {log_path}")
    print("Ejecuta 'python analysis.py' para ver tu reporte de estudio.")


if __name__ == "__main__":
    main()
