// Worker: Apple Vision's document reader (RecognizeDocumentsRequest, macOS 26+) — docs/v2_ocr_engines.md; protocol
// in parserx/tool_eval/ocr_engines.py: one JSON request per stdin line ({"image": path}), one JSON answer per line.
//
// Build: swiftc -O scripts/ocr_engines/apple_vision.swift -o ~/parserx-exp/ocr-engines/apple-vision/vision-worker
//
// With the argument --lines it uses the line recognizer instead (RecognizeTextRequest, accurate): text lines with
// their boxes, top to bottom — the kind of reading ParserX's local reading (RapidOCR) gives.
//
// Vision gives paragraphs, tables (cells with row and column ranges) and lists, each with its region, but no reading
// order across them and no labels such as header or figure.  The Markdown follows Vision's paragraph order; a table
// or list goes before the first paragraph below its top edge that it overlaps horizontally; paragraphs inside a table
// or list are left out (they are written there).

import Foundation
import Vision

struct Box: Codable { let x0, y0, x1, y1: Double }  // normalized, top-left origin

func box(_ region: NormalizedRegion) -> Box {
    let r = region.boundingBox.cgRect  // normalized, bottom-left origin
    return Box(x0: r.minX, y0: 1 - r.maxY, x1: r.maxX, y1: 1 - r.minY)
}

func inside(_ a: Box, _ b: Box) -> Bool {
    let cx = (a.x0 + a.x1) / 2, cy = (a.y0 + a.y1) / 2
    return cx >= b.x0 && cx <= b.x1 && cy >= b.y0 && cy <= b.y1
}

func overlapsHorizontally(_ a: Box, _ b: Box) -> Bool { min(a.x1, b.x1) > max(a.x0, b.x0) }

func escape(_ s: String) -> String {
    s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
        .replacingOccurrences(of: ">", with: "&gt;")
}

func tableHTML(_ table: DocumentObservation.Container.Table) -> String {
    var html = "<table>"
    var seen = Set<String>()
    for (r, row) in table.rows.enumerated() {
        html += "<tr>"
        for cell in row {
            let key = "\(cell.rowRange.lowerBound),\(cell.columnRange.lowerBound)"
            if cell.rowRange.lowerBound != r || seen.contains(key) { continue }
            seen.insert(key)
            var attrs = ""
            if cell.rowRange.count > 1 { attrs += " rowspan=\"\(cell.rowRange.count)\"" }
            if cell.columnRange.count > 1 { attrs += " colspan=\"\(cell.columnRange.count)\"" }
            let text = cell.content.text.transcript.replacingOccurrences(of: "\n", with: " ")
            html += "<td\(attrs)>\(escape(text))</td>"
        }
        html += "</tr>"
    }
    return html + "</table>"
}

struct Answer: Codable {
    var markdown: String = ""
    var blocks: [[String: String]] = []
    var seconds: Double = 0
    var error: String? = nil
}

func read(_ path: String) async -> Answer {
    let started = Date()
    var answer = Answer()
    do {
        var request = RecognizeDocumentsRequest()
        request.textRecognitionOptions.recognitionLanguages = [
            Locale.Language(identifier: "zh-Hans"), Locale.Language(identifier: "en-US"),
        ]
        request.textRecognitionOptions.automaticallyDetectLanguage = true
        let observations = try await request.perform(on: URL(fileURLWithPath: path))
        guard let document = observations.first?.document else {
            answer.seconds = Date().timeIntervalSince(started)
            return answer
        }
        // Tables and lists as pieces with their regions, placed among the paragraphs.
        var pieces: [(Box, String, String)] = document.tables.map { (box($0.boundingRegion), tableHTML($0), "table") }
        pieces += document.lists.map { list in
            (box(list.boundingRegion), list.items.map { "- " + $0.itemString }.joined(separator: "\n"), "list")
        }
        var placed = Array(repeating: false, count: pieces.count)
        var parts: [String] = []
        let title = document.title?.transcript
        for paragraph in document.paragraphs {
            let b = box(paragraph.boundingRegion)
            if pieces.contains(where: { inside(b, $0.0) }) { continue }
            for (i, piece) in pieces.enumerated() where !placed[i] && piece.0.y0 <= b.y0 && overlapsHorizontally(piece.0, b) {
                parts.append(piece.1)
                placed[i] = true
                answer.blocks.append(["label": piece.2, "bbox": "\(piece.0)"])
            }
            let text = paragraph.transcript
            parts.append(text == title ? "# " + text : text)
            answer.blocks.append(["label": text == title ? "title" : "paragraph", "bbox": "\(b)", "content": text])
        }
        for (i, piece) in pieces.enumerated() where !placed[i] {
            parts.append(piece.1)
            answer.blocks.append(["label": piece.2, "bbox": "\(piece.0)"])
        }
        answer.markdown = parts.joined(separator: "\n\n")
    } catch {
        answer.error = "\(error)"
    }
    answer.seconds = Date().timeIntervalSince(started)
    return answer
}

func readLines(_ path: String) async -> Answer {
    let started = Date()
    var answer = Answer()
    do {
        var request = RecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = [Locale.Language(identifier: "zh-Hans"), Locale.Language(identifier: "en-US")]
        request.usesLanguageCorrection = true
        let lines = try await request.perform(on: URL(fileURLWithPath: path))
            .map { (box($0.boundingRegion), $0.transcript) }
            .sorted { ($0.0.y0, $0.0.x0) < ($1.0.y0, $1.0.x0) }
        answer.markdown = lines.map { $0.1 }.joined(separator: "\n\n")
        answer.blocks = lines.map { ["label": "line", "bbox": "\($0.0)", "content": $0.1] }
    } catch {
        answer.error = "\(error)"
    }
    answer.seconds = Date().timeIntervalSince(started)
    return answer
}

let linesMode = CommandLine.arguments.contains("--lines")
let encoder = JSONEncoder()
while let line = readLine() {
    guard let data = line.data(using: .utf8),
          let request = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let path = request["image"] as? String else { continue }
    let answer = linesMode ? await readLines(path) : await read(path)
    let out = (try? encoder.encode(answer)).flatMap { String(data: $0, encoding: .utf8) } ?? "{\"error\": \"encode\"}"
    print(out)
    fflush(stdout)
}
