# Copyright (c) 2023, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import time
from dataclasses import dataclass, field
from typing import List

PLAYERRESX = 384
PLAYERRESY = 288
MARGINL = 10
MARGINR = 10

from utils.constants import BLANK_TOKEN, SPACE_TOKEN
from utils.data_prep import Segment, Token, Utterance, Word


def seconds_to_ass_format(seconds_float):
    seconds_float = float(seconds_float)
    mm, ss_decimals = divmod(seconds_float, 60)
    hh, mm = divmod(mm, 60)

    hh = str(round(hh))
    if len(hh) == 1:
        hh = '0' + hh

    mm = str(round(mm))
    if len(mm) == 1:
        mm = '0' + mm

    ss_decimals = f"{ss_decimals:.2f}"
    if len(ss_decimals.split(".")[0]) == 1:
        ss_decimals = "0" + ss_decimals

    srt_format_time = f"{hh}:{mm}:{ss_decimals}"

    return srt_format_time


def make_ass_files(
    utt_obj, model, output_dir_root, minimum_timestamp_duration, ass_file_config,
):

    # don't try to make files if utt_obj.segments_and_tokens is empty, which will happen
    # in the case of the ground truth text being empty or the number of tokens being too large vs audio duration
    if not utt_obj.segments_and_tokens:
        return utt_obj

    if ass_file_config.resegment_text_to_fill_space:
        utt_obj = resegment_utt_obj(utt_obj, ass_file_config)

    utt_obj = make_word_level_ass_file(utt_obj, model, output_dir_root, minimum_timestamp_duration, ass_file_config,)
    utt_obj = make_token_level_ass_file(utt_obj, model, output_dir_root, minimum_timestamp_duration, ass_file_config,)

    return utt_obj


def _get_word_n_chars(word):
    n_chars = 0
    for token in word.tokens:
        if token.text != BLANK_TOKEN:
            n_chars += len(token.text)
    return n_chars


def _get_segment_n_chars(segment):
    n_chars = 0
    for word_or_token in segment.words_and_tokens:
        if word_or_token.text == SPACE_TOKEN:
            n_chars += 1
        elif word_or_token.text != BLANK_TOKEN:
            n_chars += len(word_or_token.text)
    return n_chars


def resegment_utt_obj(utt_obj, ass_file_config):

    # get list of just all words and tokens
    all_words_and_tokens = []
    for segment_or_token in utt_obj.segments_and_tokens:
        if type(segment_or_token) is Segment:
            all_words_and_tokens.extend(segment_or_token.words_and_tokens)
        else:
            all_words_and_tokens.append(segment_or_token)

    # figure out how many chars will fit into one 'slide' and thus should be the max
    # size of a segment
    approx_chars_per_line = (PLAYERRESX - MARGINL - MARGINR) / (
        ass_file_config.fontsize * 0.6
    )  # assume chars 0.6 as wide as they are tall
    approx_lines_per_segment = (PLAYERRESY - ass_file_config.marginv) / (
        ass_file_config.fontsize * 1.15
    )  # assume line spacing is 1.15
    if approx_lines_per_segment > ass_file_config.max_lines_per_segment:
        approx_lines_per_segment = ass_file_config.max_lines_per_segment

    max_chars_per_segment = int(approx_chars_per_line * approx_lines_per_segment)

    new_segments_and_tokens = []
    all_words_and_tokens_pointer = 0
    for word_or_token in all_words_and_tokens:
        if type(word_or_token) is Token:
            new_segments_and_tokens.append(word_or_token)
            all_words_and_tokens_pointer += 1
        else:
            break

    new_segments_and_tokens.append(Segment())

    while all_words_and_tokens_pointer < len(all_words_and_tokens):
        word_or_token = all_words_and_tokens[all_words_and_tokens_pointer]
        if type(word_or_token) is Word:

            # if this is going to be the first word in the segment, we definitely want
            # to add it to the segment
            if not new_segments_and_tokens[-1].words_and_tokens:
                new_segments_and_tokens[-1].words_and_tokens.append(word_or_token)

            else:
                # if not the first word, check what the new length of the segment will be
                # if short enough - add this word to this segment;
                # if too long - add to a new segment
                this_word_n_chars = _get_word_n_chars(word_or_token)
                segment_so_far_n_chars = _get_segment_n_chars(new_segments_and_tokens[-1])
                if this_word_n_chars + segment_so_far_n_chars < max_chars_per_segment:
                    new_segments_and_tokens[-1].words_and_tokens.append(word_or_token)
                else:
                    new_segments_and_tokens.append(Segment())
                    new_segments_and_tokens[-1].words_and_tokens.append(word_or_token)

        else:  # i.e. word_or_token is a token
            # currently this breaks the convention of tokens at the end/beginning
            # of segments being listed as separate tokens in segment.word_and_tokens
            # TODO: change code so we follow this convention
            new_segments_and_tokens[-1].words_and_tokens.append(word_or_token)

        all_words_and_tokens_pointer += 1

    utt_obj.segments_and_tokens = new_segments_and_tokens

    return utt_obj


def make_word_level_ass_file(
    utt_obj, model, output_dir_root, minimum_timestamp_duration, ass_file_config,
):

    default_style_dict = {
        "Name": "Default",
        "Fontname": "Arial",
        "Fontsize": str(ass_file_config.fontsize),
        "PrimaryColour": "&Hffffff",
        "SecondaryColour": "&Hffffff",
        "OutlineColour": "&H0",
        "BackColour": "&H0",
        "Bold": "0",
        "Italic": "0",
        "Underline": "0",
        "StrikeOut": "0",
        "ScaleX": "100",
        "ScaleY": "100",
        "Spacing": "0",
        "Angle": "0",
        "BorderStyle": "1",
        "Outline": "1",
        "Shadow": "0",
        "Alignment": "2",
        "MarginL": str(MARGINL),
        "MarginR": str(MARGINR),
        "MarginV": str(ass_file_config.marginv),
        "Encoding": "0",
    }

    output_dir = os.path.join(output_dir_root, "ass", "words")
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f"{utt_obj.utt_id}.ass")

    with open(output_file, 'w') as f:
        default_style_top_line = "Format: " + ", ".join(default_style_dict.keys())
        default_style_bottom_line = "Style: " + ",".join(default_style_dict.values())

        f.write(
            (
                "[Script Info]\n"
                "ScriptType: v4.00+\n"
                f"PlayResX: {PLAYERRESX}\n"
                f"PlayResY: {PLAYERRESY}\n"
                "\n"
                "[V4+ Styles]\n"
                f"{default_style_top_line}\n"
                f"{default_style_bottom_line}\n"
                "\n"
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n\n"
            )
        )

        # write first set of subtitles for text before speech starts to be spoken
        words_in_first_segment = []
        for segment_or_token in utt_obj.segments_and_tokens:
            if type(segment_or_token) is Segment:
                first_segment = segment_or_token

                for word_or_token in first_segment.words_and_tokens:
                    if type(word_or_token) is Word:
                        words_in_first_segment.append(word_or_token)
                break

        text_before_speech = r"{\c&c7c1c2&}" + " ".join([x.text for x in words_in_first_segment]) + r"{\r}"
        subtitle_text = (
            f"Dialogue: 0,{seconds_to_ass_format(0)},{seconds_to_ass_format(words_in_first_segment[0].t_start)},Default,,0,0,0,,"
            + text_before_speech.rstrip()
        )

        f.write(subtitle_text + '\n')

        for segment_or_token in utt_obj.segments_and_tokens:
            if type(segment_or_token) is Segment:
                segment = segment_or_token

                words_in_segment = []
                for word_or_token in segment.words_and_tokens:
                    if type(word_or_token) is Word:
                        words_in_segment.append(word_or_token)

                for word_i, word in enumerate(words_in_segment):

                    text_before = " ".join([x.text for x in words_in_segment[:word_i]])
                    if text_before != "":
                        text_before += " "
                    text_before = r"{\c&H3d2e31&}" + text_before + r"{\r}"

                    if word_i < len(words_in_segment) - 1:
                        text_after = " " + " ".join([x.text for x in words_in_segment[word_i + 1 :]])
                    else:
                        text_after = ""
                    text_after = r"{\c&c7c1c2&}" + text_after + r"{\r}"

                    aligned_text = r"{\c&H09ab39&}" + word.text + r"{\r}"
                    aligned_text_off = r"{\c&H3d2e31&}" + word.text + r"{\r}"

                    subtitle_text = (
                        f"Dialogue: 0,{seconds_to_ass_format(word.t_start)},{seconds_to_ass_format(word.t_end)},Default,,0,0,0,,"
                        + text_before
                        + aligned_text
                        + text_after.rstrip()
                    )
                    f.write(subtitle_text + '\n')

                    # add subtitles without word-highlighting for when words are not being spoken
                    if word_i < len(words_in_segment) - 1:
                        last_word_end = float(words_in_segment[word_i].t_end)
                        next_word_start = float(words_in_segment[word_i + 1].t_start)
                        if next_word_start - last_word_end > 0.001:
                            subtitle_text = (
                                f"Dialogue: 0,{seconds_to_ass_format(last_word_end)},{seconds_to_ass_format(next_word_start)},Default,,0,0,0,,"
                                + text_before
                                + aligned_text_off
                                + text_after.rstrip()
                            )
                            f.write(subtitle_text + '\n')

    utt_obj.saved_output_files[f"words_level_ass_filepath"] = output_file

    return utt_obj


def make_token_level_ass_file(
    utt_obj, model, output_dir_root, minimum_timestamp_duration, ass_file_config,
):

    default_style_dict = {
        "Name": "Default",
        "Fontname": "Arial",
        "Fontsize": str(ass_file_config.fontsize),
        "PrimaryColour": "&Hffffff",
        "SecondaryColour": "&Hffffff",
        "OutlineColour": "&H0",
        "BackColour": "&H0",
        "Bold": "0",
        "Italic": "0",
        "Underline": "0",
        "StrikeOut": "0",
        "ScaleX": "100",
        "ScaleY": "100",
        "Spacing": "0",
        "Angle": "0",
        "BorderStyle": "1",
        "Outline": "1",
        "Shadow": "0",
        "Alignment": "2",
        "MarginL": str(MARGINL),
        "MarginR": str(MARGINR),
        "MarginV": str(ass_file_config.marginv),
        "Encoding": "0",
    }

    output_dir = os.path.join(output_dir_root, "ass", "tokens")
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f"{utt_obj.utt_id}.ass")

    with open(output_file, 'w') as f:
        default_style_top_line = "Format: " + ", ".join(default_style_dict.keys())
        default_style_bottom_line = "Style: " + ",".join(default_style_dict.values())

        f.write(
            (
                "[Script Info]\n"
                "ScriptType: v4.00+\n"
                f"PlayResX: {PLAYERRESX}\n"
                f"PlayResY: {PLAYERRESY}\n"
                "ScaledBorderAndShadow: yes\n"
                "\n"
                "[V4+ Styles]\n"
                f"{default_style_top_line}\n"
                f"{default_style_bottom_line}\n"
                "\n"
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n\n"
            )
        )

        # write first set of subtitles for text before speech starts to be spoken
        tokens_in_first_segment = []
        for segment_or_token in utt_obj.segments_and_tokens:
            if type(segment_or_token) is Segment:
                for word_or_token in segment_or_token.words_and_tokens:
                    if type(word_or_token) is Token:
                        if word_or_token.text != BLANK_TOKEN:
                            tokens_in_first_segment.append(word_or_token)
                    else:
                        for token in word_or_token.tokens:
                            if token.text != BLANK_TOKEN:
                                tokens_in_first_segment.append(token)

                break

        for token in tokens_in_first_segment:
            token.text_cased = token.text_cased.replace(
                "▁", " "
            )  # replace underscores used in subword tokens with spaces
            token.text_cased = token.text_cased.replace(SPACE_TOKEN, " ")  # space token with actual space

        text_before_speech = r"{\c&c7c1c2&}" + "".join([x.text_cased for x in tokens_in_first_segment]) + r"{\r}"
        subtitle_text = (
            f"Dialogue: 0,{seconds_to_ass_format(0)},{seconds_to_ass_format(tokens_in_first_segment[0].t_start)},Default,,0,0,0,,"
            + text_before_speech.rstrip()
        )

        f.write(subtitle_text + '\n')

        for segment_or_token in utt_obj.segments_and_tokens:
            if type(segment_or_token) is Segment:
                segment = segment_or_token

                tokens_in_segment = []  # make list of (non-blank) tokens
                for word_or_token in segment.words_and_tokens:
                    if type(word_or_token) is Token:
                        if word_or_token.text != BLANK_TOKEN:
                            tokens_in_segment.append(word_or_token)
                    else:
                        for token in word_or_token.tokens:
                            if token.text != BLANK_TOKEN:
                                tokens_in_segment.append(token)

                for token in tokens_in_segment:
                    token.text_cased = token.text_cased.replace(
                        "▁", " "
                    )  # replace underscores used in subword tokens with spaces
                    token.text_cased = token.text_cased.replace(SPACE_TOKEN, " ")  # space token with actual space

                for token_i, token in enumerate(tokens_in_segment):

                    text_before = "".join([x.text_cased for x in tokens_in_segment[:token_i]])
                    text_before = r"{\c&H3d2e31&}" + text_before + r"{\r}"

                    if token_i < len(tokens_in_segment) - 1:
                        text_after = "".join([x.text_cased for x in tokens_in_segment[token_i + 1 :]])
                    else:
                        text_after = ""
                    text_after = r"{\c&c7c1c2&}" + text_after + r"{\r}"

                    aligned_text = r"{\c&H09ab39&}" + token.text_cased + r"{\r}"
                    aligned_text_off = r"{\c&H3d2e31&}" + token.text_cased + r"{\r}"

                    subtitle_text = (
                        f"Dialogue: 0,{seconds_to_ass_format(token.t_start)},{seconds_to_ass_format(token.t_end)},Default,,0,0,0,,"
                        + text_before
                        + aligned_text
                        + text_after.rstrip()
                    )
                    f.write(subtitle_text + '\n')

                    # add subtitles without word-highlighting for when words are not being spoken
                    if token_i < len(tokens_in_segment) - 1:
                        last_token_end = float(tokens_in_segment[token_i].t_end)
                        next_token_start = float(tokens_in_segment[token_i + 1].t_start)
                        if next_token_start - last_token_end > 0.001:
                            subtitle_text = (
                                f"Dialogue: 0,{seconds_to_ass_format(last_token_end)},{seconds_to_ass_format(next_token_start)},Default,,0,0,0,,"
                                + text_before
                                + aligned_text_off
                                + text_after.rstrip()
                            )
                            f.write(subtitle_text + '\n')

    utt_obj.saved_output_files[f"tokens_level_ass_filepath"] = output_file

    return utt_obj
