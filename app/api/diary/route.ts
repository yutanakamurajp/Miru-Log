import { NextResponse } from "next/server";
import { z } from "zod";

const diaryEntrySchema = z.object({
  title: z.string().min(1).max(100),
  content: z.string().min(1),
});

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const parsedBody = diaryEntrySchema.parse(body);

    // Save to database or any other logic here

    return NextResponse.json({ message: "Diary entry created", data: parsedBody }, { status: 201 });
  } catch (error) {
    if (error instanceof z.ZodError) {
      return NextResponse.json({ message: "Invalid input", errors: error.errors }, { status: 400 });
    }
    return NextResponse.json({ message: "Internal server error" }, { status: 500 });
  }
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);

  // Pagination parameters
  const page = Math.max(1, parseInt(searchParams.get("page") || "1", 10));
  const limit = Math.min(100, Math.max(1, parseInt(searchParams.get("limit") || "10", 10)));

  try {
    // Fetch diary entries from database or any other source
    const diaryEntries = []; // Replace with actual data fetching logic

    return NextResponse.json({ message: "Diary entries fetched", data: diaryEntries }, { status: 200 });
  } catch (error) {
    return NextResponse.json({ message: "Internal server error" }, { status: 500 });
  }
}